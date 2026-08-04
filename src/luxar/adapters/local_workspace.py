"""本地工作区适配器：受控读取和修改项目目录内已有的 ESP-IDF 源码文件。"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from luxar.domain.repairs import ProjectFile, RepairPlan
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


@dataclass
class _PreparedReplacement:
    relative_path: str
    target: Path
    original_bytes: bytes
    replacement_bytes: bytes
    staged_path: Path | None = None


def _resolve_repair_target(
    root: Path,
    relative_path: str,
) -> Path:
    path_parts = PurePosixPath(relative_path).parts

    if (
        not _is_allowed_file_name(path_parts[-1])
        or any(
            _is_excluded_directory_name(part)
            for part in path_parts[:-1]
        )
    ):
        raise WorkspaceError(
            category="unsupported_file",
            message="修复目标不属于允许的项目源码",
            retryable=False,
        )

    target = root.joinpath(*path_parts)
    _assert_no_link_components(root, target)

    try:
        if not target.exists() or not target.is_file():
            raise WorkspaceError(
                category="invalid_project",
                message="修复目标必须是已经存在的文件",
                retryable=False,
            )

        resolved_target = target.resolve(strict=True)
        resolved_target.relative_to(root)

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
            message="工作区文件检查失败",
            retryable=True,
        ) from error

    _assert_no_link_components(root, target)

    return target


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

    def _prepare_replacements(
        self,
        root: Path,
        repair: RepairPlan,
    ) -> list[_PreparedReplacement]:
        prepared: list[_PreparedReplacement] = []
        original_total_bytes = 0
        replacement_total_bytes = 0

        for replacement in repair.replacements:
            target = _resolve_repair_target(
                root,
                replacement.path,
            )

            try:
                original_stat_size = target.stat().st_size
            except OSError as error:
                raise WorkspaceError(
                    category="io",
                    message="工作区文件读取失败",
                    retryable=True,
                ) from error

            if original_stat_size > self.max_file_bytes:
                raise WorkspaceError(
                    category="file_too_large",
                    message="单个项目文件超过处理上限",
                    retryable=False,
                )

            _assert_no_link_components(root, target)

            try:
                original_bytes = target.read_bytes()
            except OSError as error:
                raise WorkspaceError(
                    category="io",
                    message="工作区文件读取失败",
                    retryable=True,
                ) from error

            replacement_bytes = replacement.content.encode("utf-8")

            if (
                len(original_bytes) > self.max_file_bytes
                or len(replacement_bytes) > self.max_file_bytes
            ):
                raise WorkspaceError(
                    category="file_too_large",
                    message="单个项目文件超过处理上限",
                    retryable=False,
                )

            if (
                b"\x00" in original_bytes
                or b"\x00" in replacement_bytes
            ):
                raise WorkspaceError(
                    category="invalid_encoding",
                    message="项目源码必须是 UTF-8 文本",
                    retryable=False,
                )

            try:
                original_bytes.decode(
                    "utf-8",
                    errors="strict",
                )
            except UnicodeDecodeError as error:
                raise WorkspaceError(
                    category="invalid_encoding",
                    message="项目源码必须是 UTF-8 文本",
                    retryable=False,
                ) from error

            original_total_bytes += len(original_bytes)
            replacement_total_bytes += len(replacement_bytes)

            if (
                original_total_bytes > self.max_total_bytes
                or replacement_total_bytes > self.max_total_bytes
            ):
                raise WorkspaceError(
                    category="context_too_large",
                    message="本次修复文件总量超过处理上限",
                    retryable=False,
                )

            prepared.append(
                _PreparedReplacement(
                    relative_path=replacement.path,
                    target=target,
                    original_bytes=original_bytes,
                    replacement_bytes=replacement_bytes,
                )
            )

        return prepared

    def _rollback_committed(
        self,
        root: Path,
        committed: list[_PreparedReplacement],
    ) -> None:
        rollback_failed = False

        # 后提交的文件先恢复，顺序与数据库事务回滚相似。
        for item in reversed(committed):
            rollback_staged_path: Path | None = None

            try:
                _assert_no_link_components(
                    root,
                    item.target,
                )

                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=item.target.parent,
                    prefix=".luxar-",
                    suffix=".tmp",
                    delete=False,
                ) as rollback_file:
                    rollback_staged_path = Path(
                        rollback_file.name
                    )
                    rollback_file.write(
                        item.original_bytes
                    )
                    rollback_file.flush()

                os.replace(
                    rollback_staged_path,
                    item.target,
                )
                rollback_staged_path = None

            except (OSError, WorkspaceError):
                # 记住失败，但继续清理并尝试恢复其他文件。
                rollback_failed = True

            finally:
                if rollback_staged_path is not None:
                    try:
                        rollback_staged_path.unlink(
                            missing_ok=True
                        )
                    except OSError:
                        rollback_failed = True

        if rollback_failed:
            raise WorkspaceError(
                category="rollback_failed",
                message="工作区修复回滚失败",
                retryable=False,
            )

    def apply_repair(
        self,
        project_path: Path,
        repair: RepairPlan,
    ) -> list[str]:
        root = _resolve_project_root(project_path)
        prepared = self._prepare_replacements(
            root,
            repair,
        )
        committed: list[_PreparedReplacement] = []

        try:
            # Stage：所有新内容先写入临时文件。
            for item in prepared:
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=item.target.parent,
                        prefix=".luxar-",
                        suffix=".tmp",
                        delete=False,
                    ) as temporary_file:
                        item.staged_path = Path(
                            temporary_file.name
                        )
                        temporary_file.write(
                            item.replacement_bytes
                        )
                        temporary_file.flush()

                except OSError as error:
                    raise WorkspaceError(
                        category="io",
                        message="工作区文件写入失败",
                        retryable=True,
                    ) from error

            # Commit：全部暂存成功后才开始替换。
            for item in prepared:
                _assert_no_link_components(
                    root,
                    item.target,
                )

                try:
                    resolved_target = item.target.resolve(
                        strict=True
                    )
                    resolved_target.relative_to(root)

                    if not resolved_target.is_file():
                        raise WorkspaceError(
                            category="invalid_project",
                            message="修复目标必须是已经存在的文件",
                            retryable=False,
                        )

                    if item.staged_path is None:
                        raise WorkspaceError(
                            category="io",
                            message="工作区暂存文件无效",
                            retryable=True,
                        )

                    os.replace(
                        item.staged_path,
                        item.target,
                    )
                    item.staged_path = None
                    committed.append(item)

                except WorkspaceError:
                    raise
                except ValueError as error:
                    raise WorkspaceError(
                        category="unsafe_path",
                        message="工作区路径不能离开项目目录",
                        retryable=False,
                    ) from error
                except OSError as error:
                    raise WorkspaceError(
                        category="io",
                        message="工作区文件写入失败",
                        retryable=True,
                    ) from error

            return [
                item.relative_path
                for item in prepared
            ]

        except WorkspaceError as error:
            if committed:
                try:
                    self._rollback_committed(
                        root,
                        committed,
                    )
                except WorkspaceError as rollback_error:
                    raise rollback_error from error

            # 回滚成功后，继续抛出最初的安全错误。
            raise

        finally:
            for item in prepared:
                if item.staged_path is None:
                    continue

                try:
                    item.staged_path.unlink(missing_ok=True)
                except OSError:
                    pass
