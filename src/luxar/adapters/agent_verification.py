"""Supervisor 验证 Port 的受控本地实现。"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from luxar.adapters.espidf_common import _is_link_or_junction
from luxar.domain.agent.runtime_verification import (
    ProtocolProbeEvidence,
    ProtocolProbeSpec,
    RuntimeScenarioEvidence,
    RuntimeScenarioSpec,
)
from luxar.domain.agent.verification import (
    ComponentTestEvidence,
    ComponentTestSpec,
    FirmwareResourceEvidence,
)
from luxar.ports.verification import VerificationToolError


_PYTEST_COUNTS_RE = re.compile(
    r"(?:(?P<passed>\d+) passed)?(?:,?\s*(?P<failed>\d+) failed)?"
    r"(?:,?\s*(?P<skipped>\d+) skipped)?"
)
_CTEST_COUNTS_RE = re.compile(
    r"(?P<passed>\d+) tests? passed, (?P<failed>\d+) tests? failed",
    re.IGNORECASE,
)
_FLASH_SIZE_RE = re.compile(r'^CONFIG_ESPTOOLPY_FLASHSIZE="(?P<size>[^"]+)"$', re.MULTILINE)
_FLASH_SIZE_FLAG_RE = re.compile(
    r"^CONFIG_ESPTOOLPY_FLASHSIZE_(?P<size>\d+MB)=y$",
    re.MULTILINE,
)
_CUSTOM_PARTITION_RE = re.compile(
    r'^CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="(?P<path>[^"]+)"$',
    re.MULTILINE,
)


def _safe_root(project_path: Path) -> Path:
    try:
        if _is_link_or_junction(project_path):
            raise VerificationToolError("configuration", "项目目录不能是链接")
        root = project_path.resolve(strict=True)
        if not root.is_dir():
            raise VerificationToolError("configuration", "项目目录无效")
        return root
    except VerificationToolError:
        raise
    except (OSError, RuntimeError) as error:
        raise VerificationToolError("configuration", "项目目录无效") from error


def _safe_child(root: Path, relative: str, *, expect_directory: bool = False) -> Path:
    pure = PurePosixPath(relative.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise VerificationToolError("configuration", "目标必须是项目内相对路径")
    try:
        candidate = root.joinpath(*pure.parts)
        current = root
        for part in pure.parts:
            current = current / part
            if current.exists() and _is_link_or_junction(current):
                raise VerificationToolError("configuration", "目标路径不能经过链接")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise VerificationToolError("configuration", "目标超出项目目录")
        if expect_directory and not resolved.is_dir():
            raise VerificationToolError("configuration", "目标测试目录不存在")
        if not expect_directory and not resolved.is_file():
            raise VerificationToolError("configuration", "目标测试文件不存在")
        return resolved
    except VerificationToolError:
        raise
    except (OSError, RuntimeError) as error:
        raise VerificationToolError("configuration", "目标路径无效") from error


def _safe_process_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "LANG",
        "LC_ALL",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _summary(stdout: str, stderr: str, root: Path, limit: int) -> str:
    text = f"{stdout}\n{stderr}".strip()
    text = text.replace(str(root), "<PROJECT>")
    return text[-limit:]


class LocalComponentTestAdapter:
    """只执行固定 runner，并把测试目标限制在项目目录内。"""

    def __init__(
        self,
        *,
        pytest_command: Sequence[str] | None = None,
        ctest_command: Sequence[str] = ("ctest",),
        max_summary_chars: int = 8_000,
    ) -> None:
        self.pytest_command = tuple(
            pytest_command or (sys.executable, "-m", "pytest", "-q")
        )
        self.ctest_command = tuple(ctest_command)
        self.max_summary_chars = max_summary_chars
        for command in (self.pytest_command, self.ctest_command):
            if not command or any(not token.strip() for token in command):
                raise ValueError("测试 runner 命令不能为空")
        if max_summary_chars < 1:
            raise ValueError("max_summary_chars 必须为正整数")

    def run_component_test(
        self,
        project_path: Path,
        spec: ComponentTestSpec,
    ) -> ComponentTestEvidence:
        root = _safe_root(project_path)
        if spec.runner == "pytest":
            target = _safe_child(root, spec.target)
            command = [*self.pytest_command, target.relative_to(root).as_posix()]
            logical_command = ["python", "-m", "pytest", spec.target]
        elif spec.runner == "ctest":
            target = _safe_child(root, spec.target, expect_directory=True)
            command = [
                *self.ctest_command,
                "--test-dir",
                str(target),
                "--output-on-failure",
            ]
            logical_command = ["ctest", "--test-dir", spec.target]
        else:
            raise VerificationToolError(
                "configuration",
                "idf_unity 需要设备审批专用执行器",
            )
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=spec.timeout_seconds,
                env=_safe_process_environment(),
            )
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout if isinstance(error.stdout, str) else ""
            stderr = error.stderr if isinstance(error.stderr, str) else ""
            return ComponentTestEvidence(
                test_id=spec.test_id,
                success=False,
                runner=spec.runner,
                command=logical_command,
                return_code=-1,
                output_summary=_summary(stdout, stderr, root, self.max_summary_chars),
            )
        except OSError as error:
            raise VerificationToolError("environment", "测试进程无法启动") from error
        passed, failed, skipped = self._parse_counts(
            spec.runner,
            f"{completed.stdout}\n{completed.stderr}",
        )
        return ComponentTestEvidence(
            test_id=spec.test_id,
            success=completed.returncode == 0,
            runner=spec.runner,
            command=logical_command,
            return_code=completed.returncode,
            passed=passed,
            failed=failed,
            skipped=skipped,
            output_summary=_summary(
                completed.stdout,
                completed.stderr,
                root,
                self.max_summary_chars,
            ),
        )

    def _parse_counts(self, runner: str, output: str) -> tuple[int, int, int]:
        if runner == "ctest":
            matches = list(_CTEST_COUNTS_RE.finditer(output))
        else:
            matches = [
                match
                for match in _PYTEST_COUNTS_RE.finditer(output)
                if any(match.groupdict().values())
            ]
        if not matches:
            return 0, 0, 0
        groups = matches[-1].groupdict()
        return tuple(int(groups.get(name) or 0) for name in ("passed", "failed", "skipped"))


def _parse_size(value: str) -> int:
    normalized = value.strip().upper()
    multiplier = 1
    if normalized.endswith("KB"):
        multiplier = 1024
        normalized = normalized[:-2]
    elif normalized.endswith("MB"):
        multiplier = 1024 * 1024
        normalized = normalized[:-2]
    elif normalized.endswith("K"):
        multiplier = 1024
        normalized = normalized[:-1]
    elif normalized.endswith("M"):
        multiplier = 1024 * 1024
        normalized = normalized[:-1]
    return int(normalized, 0) * multiplier


class EspIdfArtifactInspectorAdapter:
    """只读 ESP-IDF 构建产物和项目内分区配置，不启动外部命令。"""

    def __init__(self, *, build_directory: str = "build") -> None:
        self.build_directory = build_directory

    def inspect_firmware(self, project_path: Path) -> FirmwareResourceEvidence:
        root = _safe_root(project_path)
        build = _safe_child(root, self.build_directory, expect_directory=True)
        description = self._read_description(build)
        app_bin = self._find_app_bin(root, build, description)
        sdkconfig = _safe_child(root, "sdkconfig")
        sdkconfig_text = sdkconfig.read_text(encoding="utf-8", errors="replace")
        partition_csv = self._find_partition_csv(root, sdkconfig_text)
        app_partition_size, partition_valid = self._partition_metrics(partition_csv)
        flash_size = self._flash_size(sdkconfig_text)
        app_size = app_bin.stat().st_size
        partition_valid = partition_valid and app_size <= app_partition_size
        return FirmwareResourceEvidence(
            command=["artifact.inspect", self.build_directory],
            app_size_bytes=app_size,
            app_partition_size_bytes=app_partition_size,
            flash_size_bytes=flash_size,
            partition_table_valid=partition_valid,
            summary=(
                f"app={app_bin.relative_to(root).as_posix()}, "
                f"partition={partition_csv.relative_to(root).as_posix()}"
            ),
        )

    def _read_description(self, build: Path) -> dict[str, object]:
        path = build / "project_description.json"
        try:
            if _is_link_or_junction(path) or path.stat().st_size > 2_000_000:
                raise VerificationToolError("evidence", "构建描述文件无效")
            data = json.loads(path.read_text(encoding="utf-8"))
        except VerificationToolError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise VerificationToolError("evidence", "无法读取构建描述文件") from error
        if not isinstance(data, dict):
            raise VerificationToolError("evidence", "构建描述必须是 JSON 对象")
        return data

    def _find_app_bin(
        self,
        root: Path,
        build: Path,
        description: Mapping[str, object],
    ) -> Path:
        raw = description.get("app_bin")
        if isinstance(raw, str) and raw:
            path = Path(raw)
            if path.is_absolute():
                try:
                    resolved = path.resolve(strict=True)
                except OSError as error:
                    raise VerificationToolError("evidence", "应用镜像不存在") from error
                if not resolved.is_relative_to(build) or _is_link_or_junction(resolved):
                    raise VerificationToolError("evidence", "应用镜像超出构建目录")
                return resolved
            return _safe_child(build, raw)
        project_name = description.get("project_name")
        if isinstance(project_name, str) and project_name:
            candidate = build / f"{project_name}.bin"
            if candidate.is_file() and not _is_link_or_junction(candidate):
                return candidate.resolve(strict=True)
        raise VerificationToolError("evidence", "构建描述缺少可验证的应用镜像")

    def _find_partition_csv(self, root: Path, sdkconfig: str) -> Path:
        match = _CUSTOM_PARTITION_RE.search(sdkconfig)
        candidates = [match.group("path")] if match else ["partitions.csv"]
        for candidate in candidates:
            try:
                return _safe_child(root, candidate)
            except VerificationToolError:
                continue
        raise VerificationToolError("evidence", "项目内缺少可验证的分区 CSV")

    def _partition_metrics(self, path: Path) -> tuple[int, bool]:
        try:
            rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
            entries: list[tuple[int | None, int, str]] = []
            for row in rows:
                if not row or row[0].lstrip().startswith("#") or len(row) < 5:
                    continue
                partition_type = row[1].strip()
                offset = _parse_size(row[3]) if row[3].strip() else None
                size = _parse_size(row[4])
                entries.append((offset, size, partition_type))
        except (OSError, UnicodeError, ValueError) as error:
            raise VerificationToolError("evidence", "分区 CSV 无法解析") from error
        app_sizes = [size for _, size, kind in entries if kind == "app"]
        if not app_sizes:
            raise VerificationToolError("evidence", "分区 CSV 缺少 app 分区")
        explicit = sorted(
            (offset, offset + size)
            for offset, size, _ in entries
            if offset is not None
        )
        valid = all(end <= next_start for (_, end), (next_start, _) in zip(explicit, explicit[1:]))
        return min(app_sizes), valid

    def _flash_size(self, sdkconfig: str) -> int:
        match = _FLASH_SIZE_RE.search(sdkconfig) or _FLASH_SIZE_FLAG_RE.search(sdkconfig)
        if match is None:
            raise VerificationToolError("evidence", "sdkconfig 缺少 Flash 容量")
        try:
            return _parse_size(match.group("size"))
        except ValueError as error:
            raise VerificationToolError("evidence", "Flash 容量格式无效") from error


ProtocolProbeHandler = Callable[[ProtocolProbeSpec], ProtocolProbeEvidence]
RuntimeScenarioHandler = Callable[[RuntimeScenarioSpec], RuntimeScenarioEvidence]


class RegisteredProtocolProbeAdapter:
    def __init__(self, handlers: Mapping[str, ProtocolProbeHandler]) -> None:
        self._handlers = dict(handlers)

    def run_protocol_probe(
        self,
        project_path: Path,
        spec: ProtocolProbeSpec,
    ) -> ProtocolProbeEvidence:
        _safe_root(project_path)
        handler = self._handlers.get(spec.target_ref)
        if handler is None:
            raise VerificationToolError("configuration", "协议目标未在应用中注册")
        try:
            return ProtocolProbeEvidence.model_validate(handler(spec))
        except VerificationToolError:
            raise
        except ValidationError:
            raise
        except Exception as error:
            raise VerificationToolError("execution", "协议探测处理器执行失败") from error


class RegisteredRuntimeScenarioAdapter:
    def __init__(self, handlers: Mapping[str, RuntimeScenarioHandler]) -> None:
        self._handlers = dict(handlers)

    def run_runtime_scenario(
        self,
        project_path: Path,
        spec: RuntimeScenarioSpec,
    ) -> RuntimeScenarioEvidence:
        _safe_root(project_path)
        handler = self._handlers.get(spec.target_ref)
        if handler is None:
            raise VerificationToolError("configuration", "运行场景目标未在应用中注册")
        try:
            return RuntimeScenarioEvidence.model_validate(handler(spec))
        except VerificationToolError:
            raise
        except ValidationError:
            raise
        except Exception as error:
            raise VerificationToolError("execution", "运行场景处理器执行失败") from error


__all__ = [
    "EspIdfArtifactInspectorAdapter",
    "LocalComponentTestAdapter",
    "RegisteredProtocolProbeAdapter",
    "RegisteredRuntimeScenarioAdapter",
]
