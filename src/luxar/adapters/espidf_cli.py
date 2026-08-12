"""ESP-IDF CLI 适配器：在执行真实构建前验证项目、命令与依赖授权。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path, PureWindowsPath
from typing import Literal, Sequence, TypeAlias

import yaml

from luxar.domain.evidence import BuildDiagnostic, BuildEvidence
from luxar.ports.espidf_errors import EspIdfError


_EXCLUDED_EXACT_DIRECTORIES = frozenset(
    {
        ".git",
        ".vscode",
        ".idea",
        "build",
        "managed_components",
        "__pycache__",
    }
)

BuildErrorCategory: TypeAlias = Literal[
    "dependency",
    "environment",
    "source",
    "linker",
    "unknown",
]

_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)
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
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![\w])([A-Z]:[\\/][^\s:]+(?:[\\/][^\s:]+)*)"
)
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w.])(/(?:[^\s:]+/)*[^\s:]+)"
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


def _is_excluded_directory_name(name: str) -> bool:
    return (
        name.startswith(".")
        or name in _EXCLUDED_EXACT_DIRECTORIES
        or name.startswith("build_")
    )


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError as error:
        raise EspIdfError(
            category="invalid_project",
            message="ESP-IDF 项目路径无效",
            retryable=False,
        ) from error


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


def _coerce_timeout_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


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


def _sanitize_output(text: str, root: Path, max_chars: int) -> str:
    cleaned = _strip_ansi(text).replace("\r\n", "\n").replace("\r", "\n")
    root_text = str(root.resolve())
    root_variants = {
        root_text,
        root_text.replace("\\", "/"),
        root_text.replace("/", "\\"),
    }

    for variant in sorted(root_variants, key=len, reverse=True):
        cleaned = re.sub(
            re.escape(variant) + r"[\\/]?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

    cleaned = cleaned.replace("\\", "/")
    cleaned = _WINDOWS_ABSOLUTE_PATH_RE.sub("<external-path>", cleaned)
    cleaned = _POSIX_ABSOLUTE_PATH_RE.sub("<external-path>", cleaned)

    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars]


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
        command = tuple(idf_command)

        if not command or any(
            not isinstance(token, str) or not token.strip()
            for token in command
        ):
            raise ValueError("idf_command must contain non-empty strings")

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
        launcher = Path(self.idf_command[0])

        if launcher.is_absolute():
            if not launcher.is_file():
                raise EspIdfError(
                    category="environment",
                    message="ESP-IDF 命令不可用",
                    retryable=False,
                )
        elif shutil.which(self.idf_command[0]) is None:
            raise EspIdfError(
                category="environment",
                message="ESP-IDF 命令不可用",
                retryable=False,
            )

        for token in self.idf_command[1:]:
            configured_path = Path(token)
            if configured_path.is_absolute() and not configured_path.is_file():
                raise EspIdfError(
                    category="environment",
                    message="ESP-IDF 命令不可用",
                    retryable=False,
                )

    def _read_manifest(self, manifest: Path) -> object:
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
            return yaml.safe_load(text)
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
            try:
                total_bytes += manifest.stat().st_size
            except OSError as error:
                raise EspIdfError(
                    category="invalid_project",
                    message="ESP-IDF 依赖清单无法读取",
                    retryable=False,
                ) from error

            if total_bytes > self.max_manifest_total_bytes:
                raise EspIdfError(
                    category="invalid_project",
                    message="ESP-IDF 依赖清单总量超过大小限制",
                    retryable=False,
                )

            loaded = self._read_manifest(manifest)
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

        environment = os.environ.copy()
        environment["IDF_COMPONENT_NO_COLORS"] = "1"
        environment["IDF_COMPONENT_NO_HINTS"] = "1"

        if self.allow_dependency_downloads:
            environment.pop("IDF_COMPONENT_MANAGER", None)
        else:
            environment["IDF_COMPONENT_MANAGER"] = "0"

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
