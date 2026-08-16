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
from luxar.domain.devices import FlashEvidence, SerialPortInfo
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


def _logical_command(port: str) -> list[str]:
    # 逻辑命令只保留动作与用户显式选择的串口名。
    return ["idf.py", "-p", port, "flash"]


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
    ) -> None:
        self.idf_command = validate_idf_command_tokens(idf_command)

        limits = {
            "flash_timeout_seconds": flash_timeout_seconds,
            "max_summary_chars": max_summary_chars,
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
