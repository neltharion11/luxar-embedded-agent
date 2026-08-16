"""ESP-IDF CLI 适配器：在执行真实构建前验证项目、命令与依赖授权。

共享的启动器校验、链接检查与输出脱敏已提取到 espidf_common.py，
本模块保留构建特有的清单预检、失败分类和诊断解析。
"""

from __future__ import annotations

import re
import shutil  # 保留导入：测试通过本模块路径 monkeypatch shutil.which
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Literal, Sequence, TypeAlias

import yaml

from luxar.adapters.espidf_common import (
    _coerce_timeout_output,
    _is_excluded_directory_name,
    _is_link_or_junction,
    _sanitize_output,
    _strip_ansi,
    build_safe_idf_environment,
    validate_espidf_launcher,
    validate_idf_command_tokens,
)
from luxar.domain.evidence import BuildDiagnostic, BuildEvidence
from luxar.ports.espidf_errors import EspIdfError

# 兼容再导出：既有测试从本模块导入脱敏辅助函数。
__all__ = [
    "EspIdfCliAdapter",
    "_sanitize_output",
    "_strip_ansi",
    "_is_excluded_directory_name",
    "_is_link_or_junction",
    "_coerce_timeout_output",
]


BuildErrorCategory: TypeAlias = Literal[
    "dependency",
    "environment",
    "source",
    "linker",
    "unknown",
]

_GCC_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):"
    r"(?:(?P<column>\d+):)?\s*"
    r"(?P<severity>fatal error|error|warning):\s*"
    r"(?P<message>.+?)\s*$"
)
_CMAKE_DIAGNOSTIC_RE = re.compile(
    r"^CMake Error at (?P<file>.+?):(?P<line>\d+)"
    r"(?:\s+\([^)]+\))?:\s*$"
)

_DEPENDENCY_SIGNALS = (
    "failed to resolve component",
    "component registry",
    "managed_components",
    "dependencies.lock",
    "failed to download component",
    "cannot establish a connection to the component registry",
)
_ENVIRONMENT_SIGNALS = (
    "could not find ninja",
    "cmake was not found",
    "no module named",
    "idf_path",
    "toolchain was not found",
    "compiler is not able to compile",
)
_LINKER_SIGNALS = (
    "undefined reference",
    "multiple definition",
    "ld returned",
    "collect2: error",
)


def _resolve_project_root(project_path: Path) -> Path:
    try:
        if _is_link_or_junction(project_path):
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 项目目录无效",
                retryable=False,
            )

        if not project_path.exists() or not project_path.is_dir():
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 项目目录无效",
                retryable=False,
            )

        root = project_path.resolve(strict=True)
        cmake_file = root / "CMakeLists.txt"

        if (
            _is_link_or_junction(cmake_file)
            or not cmake_file.exists()
            or not cmake_file.is_file()
        ):
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 项目缺少有效的 CMakeLists.txt",
                retryable=False,
            )

        return root

    except EspIdfError:
        raise
    except (OSError, RuntimeError) as error:
        raise EspIdfError(
            category="invalid_project",
            message="ESP-IDF 项目目录无效",
            retryable=False,
        ) from error


def _discover_manifests(root: Path) -> list[Path]:
    manifests: list[Path] = []

    def visit(directory: Path) -> None:
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda entry: entry.name.casefold(),
            )
        except OSError as error:
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 依赖清单无法读取",
                retryable=False,
            ) from error

        for entry in entries:
            if _is_link_or_junction(entry):
                if _is_excluded_directory_name(entry.name):
                    continue

                if entry.is_dir() or entry.name == "idf_component.yml":
                    raise EspIdfError(
                        category="invalid_project",
                        message="ESP-IDF 项目路径不能经过链接",
                        retryable=False,
                    )

                continue

            if entry.is_dir():
                if not _is_excluded_directory_name(entry.name):
                    visit(entry)
            elif entry.is_file() and entry.name == "idf_component.yml":
                manifests.append(entry)

    visit(root)

    return sorted(
        manifests,
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _manifest_has_dependencies(data: object) -> bool:
    if data is None:
        data = {}

    if not isinstance(data, dict):
        raise EspIdfError(
            category="invalid_project",
            message="ESP-IDF 依赖清单无效",
            retryable=False,
        )

    dependencies = data.get("dependencies")

    if dependencies is None:
        return False

    if not isinstance(dependencies, dict):
        raise EspIdfError(
            category="invalid_project",
            message="ESP-IDF 依赖清单无效",
            retryable=False,
        )

    return bool(dependencies)


def _logical_command(action: str) -> list[str]:
    return ["idf.py", action]


def _path_inside_project(raw_path: str, root: Path) -> str | None:
    normalized = raw_path.strip().strip('"')
    root_text = str(root.resolve())

    if re.match(r"^[A-Za-z]:[\\/]", normalized):
        candidate_parts = PureWindowsPath(normalized).parts
        root_parts = PureWindowsPath(root_text).parts

        if (
            len(candidate_parts) >= len(root_parts)
            and tuple(part.casefold() for part in candidate_parts[: len(root_parts)])
            == tuple(part.casefold() for part in root_parts)
        ):
            return "/".join(candidate_parts[len(root_parts) :]) or None
        return None

    candidate = Path(normalized)
    if candidate.is_absolute():
        try:
            return candidate.relative_to(root).as_posix()
        except ValueError:
            return None

    parts = candidate.parts
    if ".." in parts:
        return None
    return candidate.as_posix().lstrip("./") or None


def _classify_failure(
    action: str,
    stdout: str,
    stderr: str,
) -> BuildErrorCategory:
    combined = f"{stdout}\n{stderr}".casefold()

    if any(signal in combined for signal in _DEPENDENCY_SIGNALS):
        return "dependency"
    if any(signal in combined for signal in _ENVIRONMENT_SIGNALS):
        return "environment"
    if any(signal in combined for signal in _LINKER_SIGNALS):
        return "linker"
    if any(
        _GCC_DIAGNOSTIC_RE.match(line)
        for line in _strip_ansi(f"{stdout}\n{stderr}").splitlines()
    ):
        return "source"
    if action == "reconfigure" and "cmake error at" in combined:
        return "source"
    return "unknown"


def _parse_diagnostics(text: str, root: Path) -> list[BuildDiagnostic]:
    lines = _strip_ansi(text).replace("\r\n", "\n").replace("\r", "\n").splitlines()
    diagnostics: list[BuildDiagnostic] = []
    seen: set[tuple[object, ...]] = set()

    for index, line in enumerate(lines):
        gcc_match = _GCC_DIAGNOSTIC_RE.match(line)
        if gcc_match is not None:
            severity = gcc_match.group("severity")
            diagnostic = BuildDiagnostic(
                file=_path_inside_project(gcc_match.group("file"), root),
                line=int(gcc_match.group("line")),
                column=(
                    int(gcc_match.group("column"))
                    if gcc_match.group("column") is not None
                    else None
                ),
                severity="warning" if severity == "warning" else "error",
                message=gcc_match.group("message").strip(),
            )
        else:
            cmake_match = _CMAKE_DIAGNOSTIC_RE.match(line)
            if cmake_match is None:
                continue

            message = "CMake configuration error"
            for next_line in lines[index + 1 :]:
                if next_line.strip():
                    message = next_line.strip()
                    break

            diagnostic = BuildDiagnostic(
                file=_path_inside_project(cmake_match.group("file"), root),
                line=int(cmake_match.group("line")),
                column=None,
                severity="error",
                message=message,
            )

        key = (
            diagnostic.file,
            diagnostic.line,
            diagnostic.column,
            diagnostic.severity,
            diagnostic.code,
            diagnostic.message,
        )
        if key not in seen:
            seen.add(key)
            diagnostics.append(diagnostic)

    return diagnostics


class EspIdfCliAdapter:
    def __init__(
        self,
        *,
        idf_command: Sequence[str] = ("idf.py",),
        allow_dependency_downloads: bool = False,
        reconfigure_timeout_seconds: int = 120,
        build_timeout_seconds: int = 600,
        max_summary_chars: int = 16_000,
        max_manifest_bytes: int = 256 * 1024,
        max_manifest_total_bytes: int = 1024 * 1024,
    ) -> None:
        command = validate_idf_command_tokens(idf_command)

        if not isinstance(allow_dependency_downloads, bool):
            raise ValueError("allow_dependency_downloads must be a boolean")

        limits = {
            "reconfigure_timeout_seconds": reconfigure_timeout_seconds,
            "build_timeout_seconds": build_timeout_seconds,
            "max_summary_chars": max_summary_chars,
            "max_manifest_bytes": max_manifest_bytes,
            "max_manifest_total_bytes": max_manifest_total_bytes,
        }

        for name, value in limits.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")

        self.idf_command = command
        self.allow_dependency_downloads = allow_dependency_downloads
        self.reconfigure_timeout_seconds = reconfigure_timeout_seconds
        self.build_timeout_seconds = build_timeout_seconds
        self.max_summary_chars = max_summary_chars
        self.max_manifest_bytes = max_manifest_bytes
        self.max_manifest_total_bytes = max_manifest_total_bytes

    def _validate_command(self) -> None:
        # 委托给共享校验器，保证三个硬件 Adapter 使用完全相同的规则。
        validate_espidf_launcher(self.idf_command)

    def _read_manifest(self, manifest: Path) -> tuple[object, int]:
        try:
            stat_size = manifest.stat().st_size
        except OSError as error:
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 依赖清单无法读取",
                retryable=False,
            ) from error

        if stat_size > self.max_manifest_bytes:
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 依赖清单超过大小限制",
                retryable=False,
            )

        try:
            data = manifest.read_bytes()
        except OSError as error:
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 依赖清单无法读取",
                retryable=False,
            ) from error

        if len(data) > self.max_manifest_bytes:
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 依赖清单超过大小限制",
                retryable=False,
            )

        if b"\x00" in data:
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 依赖清单无效",
                retryable=False,
            )

        try:
            text = data.decode("utf-8", errors="strict")
            return yaml.safe_load(text), len(data)
        except (UnicodeDecodeError, yaml.YAMLError) as error:
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 依赖清单无效",
                retryable=False,
            ) from error

    def _preflight(
        self,
        project_path: Path,
    ) -> tuple[Path, dict[str, str]]:
        root = _resolve_project_root(project_path)
        self._validate_command()
        manifests = _discover_manifests(root)
        total_bytes = 0
        has_declared_dependencies = False

        for manifest in manifests:
            loaded, actual_bytes = self._read_manifest(manifest)
            total_bytes += actual_bytes

            if total_bytes > self.max_manifest_total_bytes:
                raise EspIdfError(
                    category="invalid_project",
                    message="ESP-IDF 依赖清单总量超过大小限制",
                    retryable=False,
                )

            has_declared_dependencies = (
                _manifest_has_dependencies(loaded)
                or has_declared_dependencies
            )

        if has_declared_dependencies and not self.allow_dependency_downloads:
            raise EspIdfError(
                category="dependency",
                message="项目依赖需要显式授权后才能解析",
                retryable=False,
            )

        environment = build_safe_idf_environment(
            self.allow_dependency_downloads,
        )

        return root, environment

    def _run_action(
        self,
        *,
        action: Literal["reconfigure", "build"],
        root: Path,
        environment: dict[str, str],
        timeout_seconds: int,
    ) -> BuildEvidence:
        try:
            result = subprocess.run(
                [*self.idf_command, action],
                cwd=root,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_seconds,
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            raw_stdout = _coerce_timeout_output(error.stdout)
            raw_stderr = _coerce_timeout_output(error.stderr)
            return BuildEvidence(
                success=False,
                command=_logical_command(action),
                return_code=-1,
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
            return BuildEvidence(
                success=True,
                command=_logical_command(action),
                return_code=0,
                stdout_summary=stdout_summary,
                stderr_summary=stderr_summary,
            )

        return BuildEvidence(
            success=False,
            command=_logical_command(action),
            return_code=result.returncode,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            error_category=_classify_failure(
                action,
                result.stdout,
                result.stderr,
            ),
            diagnostics=_parse_diagnostics(
                f"{result.stdout}\n{result.stderr}",
                root,
            ),
        )

    def build(self, project_path: Path) -> BuildEvidence:
        root, environment = self._preflight(project_path)

        reconfigure = self._run_action(
            action="reconfigure",
            root=root,
            environment=environment,
            timeout_seconds=self.reconfigure_timeout_seconds,
        )

        if not reconfigure.success:
            return reconfigure

        return self._run_action(
            action="build",
            root=root,
            environment=environment,
            timeout_seconds=self.build_timeout_seconds,
        )
