"""受限代码 bundle 的本地事务执行器。"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from luxar.adapters.local_workspace import (
    LocalWorkspaceAdapter,
    _assert_no_link_components,
    _is_allowed_file_name,
    _is_excluded_directory_name,
    _is_link_or_junction,
    _resolve_project_root,
)
from luxar.domain.agent.code_changes import (
    ChangeBundle,
    ChangeBundleError,
    ChangeBundleValidation,
    FileChange,
    snapshot_fingerprint,
    validate_change_bundle,
)
from luxar.domain.repairs import ProjectFile
from luxar.ports.workspace_errors import WorkspaceError


@dataclass
class _PreparedChange:
    change: FileChange
    target: Path
    original_bytes: bytes | None
    replacement_bytes: bytes | None
    staged_path: Path | None = None


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalChangeBundleExecutor:
    """在项目根目录内原子提交 bundle，并在提交失败时回滚。"""

    def __init__(
        self,
        workspace: LocalWorkspaceAdapter | None = None,
    ) -> None:
        self.workspace = workspace or LocalWorkspaceAdapter()

    def _validate_target(
        self,
        root: Path,
        change: FileChange,
    ) -> Path:
        parts = PurePosixPath(change.path).parts
        if (
            not parts
            or not _is_allowed_file_name(parts[-1])
            or any(_is_excluded_directory_name(part) for part in parts[:-1])
        ):
            raise WorkspaceError(
                category="unsupported_file",
                message="代码变更目标不属于允许的项目源码",
                retryable=False,
            )

        target = root.joinpath(*parts)
        _assert_no_link_components(root, target)

        if change.operation == "create":
            if target.exists() or _is_link_or_junction(target):
                raise ChangeBundleError(
                    "conflict",
                    "create 目标文件已经存在",
                    [change.path],
                )
            return target

        if (
            not target.exists()
            or not target.is_file()
            or _is_link_or_junction(target)
        ):
            raise ChangeBundleError(
                "missing_file",
                "修改或删除目标文件不存在",
                [change.path],
            )

        return target

    def _read_original(
        self,
        target: Path,
        change: FileChange,
    ) -> bytes:
        try:
            data = target.read_bytes()
        except OSError as error:
            raise WorkspaceError(
                category="io",
                message="代码变更目标读取失败",
                retryable=True,
            ) from error

        if len(data) > self.workspace.max_file_bytes:
            raise WorkspaceError(
                category="file_too_large",
                message="代码变更目标超过单文件处理上限",
                retryable=False,
            )
        if b"\x00" in data:
            raise WorkspaceError(
                category="invalid_encoding",
                message="项目源码必须是 UTF-8 文本",
                retryable=False,
            )
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise WorkspaceError(
                category="invalid_encoding",
                message="项目源码必须是 UTF-8 文本",
                retryable=False,
            ) from error

        if change.expected_sha256 is None:
            raise ChangeBundleError(
                "stale_snapshot",
                "修改或删除已有文件必须提供 expected_sha256",
                [change.path],
            )
        if _content_hash(data) != change.expected_sha256:
            raise ChangeBundleError(
                "stale_snapshot",
                "目标文件已变化，拒绝覆盖新内容",
                [change.path],
            )
        return data

    def _prepare_changes(
        self,
        root: Path,
        bundle: ChangeBundle,
    ) -> list[_PreparedChange]:
        prepared: list[_PreparedChange] = []
        replacement_total_bytes = 0

        for change in bundle.changes:
            target = self._validate_target(root, change)
            original_bytes: bytes | None = None
            replacement_bytes: bytes | None = None

            if change.operation != "create":
                original_bytes = self._read_original(target, change)

            if change.content is not None:
                replacement_bytes = change.content.encode("utf-8")
                if (
                    len(replacement_bytes) > self.workspace.max_file_bytes
                    or b"\x00" in replacement_bytes
                ):
                    raise WorkspaceError(
                        category=(
                            "invalid_encoding"
                            if b"\x00" in replacement_bytes
                            else "file_too_large"
                        ),
                        message=(
                            "项目源码不能包含 NUL 字节"
                            if b"\x00" in replacement_bytes
                            else "代码变更内容超过单文件处理上限"
                        ),
                        retryable=False,
                    )
                replacement_total_bytes += len(replacement_bytes)

            prepared.append(
                _PreparedChange(
                    change=change,
                    target=target,
                    original_bytes=original_bytes,
                    replacement_bytes=replacement_bytes,
                )
            )

        if replacement_total_bytes > self.workspace.max_total_bytes:
            raise WorkspaceError(
                category="context_too_large",
                message="代码变更内容总量超过处理上限",
                retryable=False,
            )
        return prepared

    @staticmethod
    def _ensure_parent_directories(
        root: Path,
        prepared: Iterable[_PreparedChange],
    ) -> list[Path]:
        created: list[Path] = []
        parents = sorted(
            {item.target.parent for item in prepared},
            key=lambda path: len(path.parts),
        )
        for parent in parents:
            try:
                relative = parent.relative_to(root)
            except ValueError as error:
                raise WorkspaceError(
                    category="unsafe_path",
                    message="代码变更目录不能离开项目目录",
                    retryable=False,
                ) from error

            current = root
            for part in relative.parts:
                current = current / part
                if current.exists():
                    if not current.is_dir() or _is_link_or_junction(current):
                        raise WorkspaceError(
                            category="unsafe_path",
                            message="代码变更目录不能经过链接或文件",
                            retryable=False,
                        )
                    continue
                try:
                    current.mkdir()
                except OSError as error:
                    raise WorkspaceError(
                        category="io",
                        message="代码变更目录创建失败",
                        retryable=True,
                    ) from error
                created.append(current)
        return created

    @staticmethod
    def _cleanup_created_directories(created: Iterable[Path]) -> None:
        for directory in sorted(created, key=lambda path: len(path.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass

    def _stage(self, item: _PreparedChange) -> None:
        if item.replacement_bytes is None:
            return
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=item.target.parent,
                prefix=".luxar-change-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                item.staged_path = Path(temporary_file.name)
                temporary_file.write(item.replacement_bytes)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
        except OSError as error:
            raise WorkspaceError(
                category="io",
                message="代码变更暂存失败",
                retryable=True,
            ) from error

    def _rollback(
        self,
        root: Path,
        committed: list[_PreparedChange],
    ) -> None:
        failed = False
        for item in reversed(committed):
            try:
                _assert_no_link_components(root, item.target)
                if item.change.operation == "create":
                    if item.target.exists():
                        item.target.unlink()
                    continue

                if item.original_bytes is None:
                    raise OSError("缺少回滚原始内容")
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=item.target.parent,
                    prefix=".luxar-rollback-",
                    suffix=".tmp",
                    delete=False,
                ) as rollback_file:
                    rollback_path = Path(rollback_file.name)
                    rollback_file.write(item.original_bytes)
                    rollback_file.flush()
                    os.fsync(rollback_file.fileno())
                try:
                    os.replace(rollback_path, item.target)
                finally:
                    rollback_path.unlink(missing_ok=True)
            except (OSError, WorkspaceError):
                failed = True

        if failed:
            raise WorkspaceError(
                category="rollback_failed",
                message="代码变更回滚失败",
                retryable=False,
            )

    def execute(
        self,
        project_path: Path,
        bundle: ChangeBundle,
    ) -> ChangeBundleValidation:
        root = _resolve_project_root(project_path)
        before_files = self.workspace.read_project_files(project_path)
        validation = validate_change_bundle(bundle=bundle, files=before_files)
        prepared = self._prepare_changes(root, bundle)
        created_directories: list[Path] = []
        committed: list[_PreparedChange] = []

        try:
            created_directories = self._ensure_parent_directories(root, prepared)
            for item in prepared:
                self._stage(item)

            for item in prepared:
                _assert_no_link_components(root, item.target)
                if item.change.operation == "create":
                    if item.target.exists() or _is_link_or_junction(item.target):
                        raise ChangeBundleError(
                            "conflict",
                            "create 目标文件在提交前出现",
                            [item.change.path],
                        )
                else:
                    if item.original_bytes is None:
                        raise ChangeBundleError(
                            "stale_snapshot",
                            "缺少目标文件快照",
                            [item.change.path],
                        )
                    try:
                        current_bytes = item.target.read_bytes()
                    except OSError as error:
                        raise WorkspaceError(
                            category="io",
                            message="代码变更提交前读取失败",
                            retryable=True,
                        ) from error
                    if current_bytes != item.original_bytes:
                        raise ChangeBundleError(
                            "stale_snapshot",
                            "目标文件在提交前发生变化",
                            [item.change.path],
                        )

                if item.change.operation == "delete":
                    item.target.unlink()
                else:
                    if item.staged_path is None:
                        raise WorkspaceError(
                            category="io",
                            message="代码变更暂存文件无效",
                            retryable=True,
                        )
                    os.replace(item.staged_path, item.target)
                    item.staged_path = None
                committed.append(item)

            after_files = self.workspace.read_project_files(project_path)
            actual_after_fingerprint = snapshot_fingerprint(after_files)
            if actual_after_fingerprint != validation.after_fingerprint:
                raise WorkspaceError(
                    category="io",
                    message="代码变更提交后快照不一致",
                    retryable=True,
                )
            return validation

        except OSError as error:
            failure = WorkspaceError(
                category="io",
                message="代码变更提交失败",
                retryable=True,
            )
            if committed:
                try:
                    self._rollback(root, committed)
                except WorkspaceError as rollback_error:
                    raise rollback_error from error
            raise failure from error
        except (ChangeBundleError, WorkspaceError) as error:
            if committed:
                try:
                    self._rollback(root, committed)
                except WorkspaceError as rollback_error:
                    raise rollback_error from error
            raise
        finally:
            for item in prepared:
                if item.staged_path is None:
                    continue
                try:
                    item.staged_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self._cleanup_created_directories(created_directories)


__all__ = ["LocalChangeBundleExecutor"]
