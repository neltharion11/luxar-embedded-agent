"""ESP-IDF 设备适配器：串口发现与 idf.py flash 的真实硬件能力。

S4 将在此基础上加入 idf.py monitor 的受控采集。所有串口名都必须
通过平台模式校验，所有命令都是参数列表、shell=False、限时执行，
输出统一脱敏限长。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Sequence

import serial.tools.list_ports

from luxar.adapters.espidf_cli import _ENVIRONMENT_SIGNALS, _resolve_project_root
from luxar.adapters.espidf_common import (
    _coerce_timeout_output,
    _sanitize_output,
    build_safe_idf_environment,
    validate_espidf_launcher,
    validate_idf_command_tokens,
)
from luxar.domain.devices import (
    DeviceLogDiagnostic,
    FlashEvidence,
    MonitorEvidence,
    SerialPortInfo,
)
from luxar.ports.espidf_errors import EspIdfError

_WINDOWS_PORT_RE = re.compile(r"^COM[1-9]\d*$")
_POSIX_PORT_RE = re.compile(r"^/dev/tty(?:USB|ACM|S)\d+$")

_SERIAL_SIGNALS = (
    "could not open port",
    "cannot open",
    "serial port",
    "port is busy",
    "permission denied",
    "no serial data received",
    "failed to connect",
)

_MAX_DESCRIPTION_CHARS = 200

# 每种日志模式的单次采集内最多保留数量，防止 State 无限膨胀。
_MAX_DIAGNOSTICS_PER_KIND: dict[str, int] = {
    "panic": 1,
    "abort": 1,
    "assert": 1,
    "watchdog": 1,
    "boot_loop": 1,
    "error": 5,
    "warning": 5,
    "unknown": 1,
}


def _logical_command(port: str) -> list[str]:
    # 逻辑命令只保留动作与用户显式选择的串口名。
    return ["idf.py", "-p", port, "flash"]


def _logical_monitor_command(port: str) -> list[str]:
    return ["idf.py", "-p", port, "monitor"]


def _parse_device_diagnostics(
    log_text: str,
) -> list[DeviceLogDiagnostic]:
    """从脱敏后的串口日志中提取 ESP32 常见故障模式。"""

    lines = log_text.splitlines()
    diagnostics: list[DeviceLogDiagnostic] = []
    claimed: set[int] = set()
    kind_counts: dict[str, int] = {}

    def add(
        kind: str,
        summary: str,
        index: int,
        context_lines: int,
    ) -> None:
        cap = _MAX_DIAGNOSTICS_PER_KIND.get(kind, 1)
        if kind_counts.get(kind, 0) >= cap:
            return

        excerpt = [
            line.strip()
            for line in lines[index : index + context_lines]
            if line.strip()
        ]
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        diagnostics.append(
            DeviceLogDiagnostic(
                kind=kind,  # type: ignore[arg-type]
                summary=summary,
                lines=excerpt,
            )
        )
        claimed.update(range(index, min(len(lines), index + context_lines)))

    # 启动回环：单次采集内出现多次复位信号。
    reset_indices = [
        index
        for index, line in enumerate(lines)
        if "rst:0x" in line.casefold()
    ]
    if len(reset_indices) >= 2:
        add(
            "boot_loop",
            "检测到重复复位，疑似启动回环",
            reset_indices[0],
            4,
        )

    for index, line in enumerate(lines):
        if index in claimed:
            continue

        lowered = line.casefold()
        if "guru meditation error" in lowered:
            add("panic", "Guru Meditation 崩溃", index, 6)
        elif "abort() was called" in lowered:
            add("abort", "abort() 被调用", index, 4)
        elif "assert failed" in lowered:
            add("assert", "断言失败", index, 4)
        elif "watchdog" in lowered or "task_wdt" in lowered:
            add("watchdog", "看门狗触发", index, 4)
        elif lowered.startswith("e ("):
            add("error", "ESP-IDF 错误日志", index, 2)
        elif lowered.startswith("w ("):
            add("warning", "ESP-IDF 警告日志", index, 2)
        elif "backtrace:" in lowered:
            add("unknown", "未归类的回溯信息", index, 6)

    return diagnostics


def _validate_port_name(port: str) -> None:
    pattern = _WINDOWS_PORT_RE if os.name == "nt" else _POSIX_PORT_RE

    if not pattern.fullmatch(port):
        raise EspIdfError(
            category="serial",
            message="串口名称无效",
            retryable=False,
        )


class EspIdfDeviceAdapter:
    def __init__(
        self,
        *,
        idf_command: Sequence[str] = ("idf.py",),
        flash_timeout_seconds: int = 300,
        max_summary_chars: int = 16_000,
        max_monitor_chars: int = 32_000,
    ) -> None:
        self.idf_command = validate_idf_command_tokens(idf_command)

        limits = {
            "flash_timeout_seconds": flash_timeout_seconds,
            "max_summary_chars": max_summary_chars,
            "max_monitor_chars": max_monitor_chars,
        }

        for name, value in limits.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")

        self.flash_timeout_seconds = flash_timeout_seconds
        self.max_summary_chars = max_summary_chars
        self.max_monitor_chars = max_monitor_chars

    def discover_serial_ports(self) -> list[SerialPortInfo]:
        ports: list[SerialPortInfo] = []

        for port in serial.tools.list_ports.comports():
            # 只暴露符合平台模式的串口，其余一律忽略。
            try:
                _validate_port_name(port.device)
            except EspIdfError:
                continue

            ports.append(
                SerialPortInfo(
                    name=port.device,
                    description=(port.description or "")[
                        :_MAX_DESCRIPTION_CHARS
                    ],
                    hardware_id=(port.hwid or "")[:_MAX_DESCRIPTION_CHARS],
                )
            )

        return sorted(
            ports,
            key=lambda port: port.name.casefold(),
        )

    def flash(
        self,
        project_path: Path,
        port: str,
    ) -> FlashEvidence:
        _validate_port_name(port)
        # 与构建 Adapter 相同的项目根校验：真实目录、无链接、有 CMakeLists。
        root = _resolve_project_root(project_path)
        validate_espidf_launcher(self.idf_command)

        # flash 不解析组件依赖，因此不扫描清单，但仍使用安全子进程环境。
        environment = build_safe_idf_environment(
            allow_dependency_downloads=False,
        )

        try:
            result = subprocess.run(
                [*self.idf_command, "-p", port, "flash"],
                cwd=root,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self.flash_timeout_seconds,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            raw_stdout = _coerce_timeout_output(error.stdout)
            raw_stderr = _coerce_timeout_output(error.stderr)
            return FlashEvidence(
                success=False,
                command=_logical_command(port),
                return_code=-1,
                port=port,
                stdout_summary=_sanitize_output(
                    raw_stdout,
                    root,
                    self.max_summary_chars,
                ),
                stderr_summary=_sanitize_output(
                    raw_stderr,
                    root,
                    self.max_summary_chars,
                ),
                error_category="timeout",
            )
        except OSError as error:
            raise EspIdfError(
                category="process",
                message="ESP-IDF 进程无法启动",
                retryable=True,
            ) from error

        stdout_summary = _sanitize_output(
            result.stdout,
            root,
            self.max_summary_chars,
        )
        stderr_summary = _sanitize_output(
            result.stderr,
            root,
            self.max_summary_chars,
        )

        if result.returncode == 0:
            return FlashEvidence(
                success=True,
                command=_logical_command(port),
                return_code=0,
                port=port,
                stdout_summary=stdout_summary,
                stderr_summary=stderr_summary,
            )

        return FlashEvidence(
            success=False,
            command=_logical_command(port),
            return_code=result.returncode,
            port=port,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            error_category=self._classify_flash_failure(
                result.stdout,
                result.stderr,
            ),
        )

    def _classify_flash_failure(
        self,
        stdout: str,
        stderr: str,
    ) -> str:
        combined = f"{stdout}\n{stderr}".casefold()

        if any(signal in combined for signal in _SERIAL_SIGNALS):
            return "serial"

        if any(signal in combined for signal in _ENVIRONMENT_SIGNALS):
            return "environment"

        return "unknown"

    def _terminate_process_tree(self, process: subprocess.Popen[str]) -> None:
        # 尽力清理：idf.py monitor 会派生自己的监控子进程，
        # 只终止直接子进程可能让子进程继续占用串口。
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            else:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        except OSError:
            pass

    def monitor(
        self,
        project_path: Path,
        port: str,
        timeout_seconds: int,
    ) -> MonitorEvidence:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive integer")

        _validate_port_name(port)
        root = _resolve_project_root(project_path)
        validate_espidf_launcher(self.idf_command)

        environment = build_safe_idf_environment(
            allow_dependency_downloads=False,
        )
        # Windows 上以独立进程组启动，超时后 taskkill /T 才能带走整棵树。
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0
        )

        try:
            process = subprocess.Popen(
                [*self.idf_command, "-p", port, "monitor"],
                cwd=root,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                creationflags=creationflags,
            )
        except OSError as error:
            raise EspIdfError(
                category="process",
                message="ESP-IDF 进程无法启动",
                retryable=True,
            ) from error

        terminated_by_timeout = False
        try:
            # 采集窗口：等待进程输出或超时，二者都是正常结果。
            stdout, stderr = process.communicate(
                timeout=timeout_seconds
            )
        except subprocess.TimeoutExpired:
            terminated_by_timeout = True
            self._terminate_process_tree(process)
            stdout, stderr = process.communicate()

        sanitized_log = _sanitize_output(
            f"{stdout or ''}\n{stderr or ''}",
            root,
            self.max_monitor_chars,
        )

        return MonitorEvidence(
            command=_logical_monitor_command(port),
            port=port,
            capture_timeout_seconds=timeout_seconds,
            captured_log=sanitized_log,
            terminated_by_timeout=terminated_by_timeout,
            diagnostics=_parse_device_diagnostics(sanitized_log),
        )
