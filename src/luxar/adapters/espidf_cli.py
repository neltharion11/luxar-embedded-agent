"""ESP-IDF CLI 适配器：在执行真实构建前验证项目、命令与依赖授权。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal, Sequence

import yaml

from luxar.domain.evidence import BuildEvidence
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
            return BuildEvidence(
                success=False,
                command=_logical_command(action),
                return_code=-1,
                stdout_summary=_coerce_timeout_output(error.stdout),
                stderr_summary=_coerce_timeout_output(error.stderr),
                error_category="timeout",
            )
        except OSError as error:
            raise EspIdfError(
                category="process",
                message="ESP-IDF 进程无法启动",
                retryable=True,
            ) from error

        if result.returncode == 0:
            return BuildEvidence(
                success=True,
                command=_logical_command(action),
                return_code=0,
                stdout_summary=result.stdout,
                stderr_summary=result.stderr,
            )

        return BuildEvidence(
            success=False,
            command=_logical_command(action),
            return_code=result.returncode,
            stdout_summary=result.stdout,
            stderr_summary=result.stderr,
            error_category="unknown",
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
