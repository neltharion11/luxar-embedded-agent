"""本地工作区适配器：受控读取和修改项目目录内已有的 ESP-IDF 源码文件。"""

from __future__ import annotations

from pathlib import Path

from luxar.domain.repairs import ProjectFile
from luxar.ports.workspace_errors import WorkspaceError


_ALLOWED_SUFFIXES = frozenset(
    {
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".s",
        ".cmake",
        ".ld",
        ".csv",
    }
)

_ALLOWED_EXACT_NAMES = frozenset(
    {
        "CMakeLists.txt",
        "Kconfig",
        "Kconfig.projbuild",
        "sdkconfig.defaults",
        "idf_component.yml",
        "project_include.cmake",
    }
)

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


def _is_allowed_file_name(name: str) -> bool:
    return (
        name in _ALLOWED_EXACT_NAMES
        or Path(name).suffix.lower() in _ALLOWED_SUFFIXES
    )


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError as error:
        raise WorkspaceError(
            category="io",
            message="工作区路径检查失败",
            retryable=True,
        ) from error


def _resolve_project_root(project_path: Path) -> Path:
    try:
        if _is_link_or_junction(project_path):
            raise WorkspaceError(
                category="unsafe_path",
                message="项目根目录不能是链接",
                retryable=False,
            )

        if not project_path.exists() or not project_path.is_dir():
            raise WorkspaceError(
                category="invalid_project",
                message="项目目录无效",
                retryable=False,
            )

        return project_path.resolve(strict=True)

    except WorkspaceError:
        raise
    except (OSError, RuntimeError) as error:
        raise WorkspaceError(
            category="invalid_project",
            message="项目目录无效",
            retryable=False,
        ) from error


def _assert_no_link_components(
    root: Path,
    target: Path,
) -> None:
    try:
        relative_path = target.relative_to(root)
    except ValueError as error:
        raise WorkspaceError(
            category="unsafe_path",
            message="工作区路径不能离开项目目录",
            retryable=False,
        ) from error

    current_path = root

    for part in relative_path.parts:
        current_path = current_path / part

        if _is_link_or_junction(current_path):
            raise WorkspaceError(
                category="unsafe_path",
                message="工作区路径不能经过链接",
                retryable=False,
            )


def _discover_allowed_files(root: Path) -> list[Path]:
    discovered_files: list[Path] = []

    def visit(directory: Path) -> None:
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda entry: entry.name.casefold(),
            )
        except OSError as error:
            raise WorkspaceError(
                category="io",
                message="工作区目录读取失败",
                retryable=True,
            ) from error

        for entry in entries:
            if _is_link_or_junction(entry):
                if _is_excluded_directory_name(entry.name):
                    continue

                if entry.is_dir() or _is_allowed_file_name(entry.name):
                    raise WorkspaceError(
                        category="unsafe_path",
                        message="工作区路径不能经过链接",
                        retryable=False,
                    )

                continue

            if entry.is_dir():
                if not _is_excluded_directory_name(entry.name):
                    visit(entry)

            elif entry.is_file() and _is_allowed_file_name(entry.name):
                discovered_files.append(entry)

    visit(root)

    return sorted(
        discovered_files,
        key=lambda path: path.relative_to(root).as_posix(),
    )


class LocalWorkspaceAdapter:
    def __init__(
        self,
        max_file_bytes: int = 256 * 1024,
        max_total_bytes: int = 1024 * 1024,
    ) -> None:
        if (
            isinstance(max_file_bytes, bool)
            or not isinstance(max_file_bytes, int)
            or max_file_bytes <= 0
        ):
            raise ValueError(
                "max_file_bytes must be a positive integer"
            )

        if (
            isinstance(max_total_bytes, bool)
            or not isinstance(max_total_bytes, int)
            or max_total_bytes <= 0
        ):
            raise ValueError(
                "max_total_bytes must be a positive integer"
            )

        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

    def read_project_files(
        self,
        project_path: Path,
    ) -> list[ProjectFile]:
        root = _resolve_project_root(project_path)
        source_paths = _discover_allowed_files(root)

        project_files: list[ProjectFile] = []
        total_bytes = 0

        for source_path in source_paths:
            _assert_no_link_components(root, source_path)

            try:
                resolved_source = source_path.resolve(strict=True)
                resolved_source.relative_to(root)

                if not resolved_source.is_file():
                    raise WorkspaceError(
                        category="invalid_project",
                        message="项目源码文件无效",
                        retryable=False,
                    )

                stat_size = resolved_source.stat().st_size

            except WorkspaceError:
                raise
            except ValueError as error:
                raise WorkspaceError(
                    category="unsafe_path",
                    message="工作区路径不能离开项目目录",
                    retryable=False,
                ) from error
            except (OSError, RuntimeError) as error:
                raise WorkspaceError(
                    category="io",
                    message="工作区文件读取失败",
                    retryable=True,
                ) from error

            if stat_size > self.max_file_bytes:
                raise WorkspaceError(
                    category="file_too_large",
                    message="单个项目文件超过读取上限",
                    retryable=False,
                )

            _assert_no_link_components(root, source_path)

            try:
                data = resolved_source.read_bytes()
            except OSError as error:
                raise WorkspaceError(
                    category="io",
                    message="工作区文件读取失败",
                    retryable=True,
                ) from error

            if len(data) > self.max_file_bytes:
                raise WorkspaceError(
                    category="file_too_large",
                    message="单个项目文件超过读取上限",
                    retryable=False,
                )

            total_bytes += len(data)

            if total_bytes > self.max_total_bytes:
                raise WorkspaceError(
                    category="context_too_large",
                    message="项目源码总量超过读取上限",
                    retryable=False,
                )

            if b"\x00" in data:
                raise WorkspaceError(
                    category="invalid_encoding",
                    message="项目源码必须是 UTF-8 文本",
                    retryable=False,
                )

            try:
                content = data.decode(
                    "utf-8",
                    errors="strict",
                )
            except UnicodeDecodeError as error:
                raise WorkspaceError(
                    category="invalid_encoding",
                    message="项目源码必须是 UTF-8 文本",
                    retryable=False,
                ) from error

            project_files.append(
                ProjectFile(
                    path=source_path.relative_to(root).as_posix(),
                    content=content,
                )
            )

        return project_files
