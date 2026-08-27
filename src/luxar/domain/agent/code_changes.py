"""受限代码变更 bundle 及写入前确定性验证。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from luxar.domain.agent.capabilities import (
    ProjectCapability,
    ProjectCapabilityExtractor,
    find_preserve_violations,
)
from luxar.domain.repairs import ProjectFile, normalize_project_relative_path


ChangeOperation = Literal["create", "modify", "replace", "delete"]


class FileChange(BaseModel):
    """一个相对路径上的原子文件变更。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    operation: ChangeOperation
    path: str
    content: str | None = None
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    summary: str = Field(default="", max_length=500)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_project_relative_path(value)

    @field_validator("expected_sha256")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is not None and any(character not in "0123456789abcdefABCDEF" for character in value):
            raise ValueError("expected_sha256 必须是十六进制 SHA-256")
        return value.lower() if value is not None else None

    @model_validator(mode="after")
    def validate_content(self) -> "FileChange":
        if self.operation in {"create", "modify", "replace"} and self.content is None:
            raise ValueError(f"{self.operation} 变更必须提供 content")
        if self.operation == "delete" and self.content is not None:
            raise ValueError("delete 变更不能提供 content")
        return self


class AppliedFileChange(BaseModel):
    """Bounded user-facing fact recorded after a transactional write succeeds."""

    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: str = Field(min_length=1, max_length=240)
    path: str = Field(min_length=1, max_length=500)
    operation: ChangeOperation
    summary: str = Field(min_length=1, max_length=500)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_project_relative_path(value)


class ChangeBundle(BaseModel):
    """一个任务范围内、可整体提交或整体拒绝的多文件变更。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    bundle_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=2000)
    changes: list[FileChange] = Field(min_length=1, max_length=100)
    allowed_paths: list[str] = Field(min_length=1, max_length=200)
    preserves: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("allowed_paths")
    @classmethod
    def normalize_allowed_paths(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            stripped = value.strip().replace("\\", "/")
            if stripped.endswith("/**"):
                base = stripped[:-3].rstrip("/")
                normalized.append(normalize_project_relative_path(base) + "/**")
            elif stripped.endswith("/*"):
                base = stripped[:-2].rstrip("/")
                normalized.append(normalize_project_relative_path(base) + "/*")
            else:
                normalized.append(normalize_project_relative_path(stripped))
        return list(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def validate_unique_and_allowed_paths(self) -> "ChangeBundle":
        paths = [change.path for change in self.changes]
        if len(paths) != len(set(paths)):
            raise ValueError("change bundle 不能重复修改同一文件")
        disallowed = [
            path for path in paths if not self.path_is_allowed(path)
        ]
        if disallowed:
            raise ValueError(f"变更路径不在任务允许范围内: {sorted(disallowed)}")
        return self

    def path_is_allowed(self, path: str) -> bool:
        normalized = normalize_project_relative_path(path)
        return any(
            normalized == allowed
            or (
                allowed.endswith("/*")
                and normalized.startswith(allowed[:-1])
                and "/" not in normalized[len(allowed) - 1 :]
            )
            or (
                allowed.endswith("/**")
                and normalized.startswith(allowed[:-2].rstrip("/") + "/")
            )
            for allowed in self.allowed_paths
        )


class ChangeBundleValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    before_fingerprint: str = Field(min_length=64, max_length=64)
    after_fingerprint: str = Field(min_length=64, max_length=64)
    before_capabilities: list[ProjectCapability] = Field(default_factory=list)
    after_capabilities: list[ProjectCapability] = Field(default_factory=list)
    changed_files: list[str] = Field(min_length=1, max_length=100)
    diff_summary: list[str] = Field(min_length=1, max_length=100)


class ChangeBundleError(ValueError):
    """变更在写入前未通过确定性验证。"""

    def __init__(self, category: str, message: str, details: Sequence[str] = ()) -> None:
        self.category = category
        self.details = tuple(details)
        super().__init__(message)


def snapshot_fingerprint(files: Sequence[ProjectFile]) -> str:
    digest = hashlib.sha256(b"luxar-change-snapshot-v1\0")
    for project_file in sorted(files, key=lambda item: item.path):
        digest.update(project_file.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(project_file.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def apply_bundle_to_snapshot(
    files: Sequence[ProjectFile],
    bundle: ChangeBundle,
) -> list[ProjectFile]:
    """在内存快照中应用 bundle，失败时不会修改输入。"""

    current = {item.path: item.content for item in files}
    for change in bundle.changes:
        if not bundle.path_is_allowed(change.path):
            raise ChangeBundleError(
                "path",
                "变更路径超出任务允许范围",
                [change.path],
            )
        exists = change.path in current
        if change.operation == "create":
            if exists:
                raise ChangeBundleError("conflict", "create 目标文件已经存在", [change.path])
            assert change.content is not None
            current[change.path] = change.content
        elif change.operation in {"modify", "replace"}:
            if not exists:
                raise ChangeBundleError("missing_file", "修改目标文件不存在", [change.path])
            if change.expected_sha256 and _content_hash(current[change.path]) != change.expected_sha256:
                raise ChangeBundleError("stale_snapshot", "目标文件已变化，拒绝覆盖新内容", [change.path])
            assert change.content is not None
            current[change.path] = change.content
        else:
            if not exists:
                raise ChangeBundleError("missing_file", "删除目标文件不存在", [change.path])
            if change.expected_sha256 and _content_hash(current[change.path]) != change.expected_sha256:
                raise ChangeBundleError("stale_snapshot", "目标文件已变化，拒绝删除", [change.path])
            del current[change.path]
    return [ProjectFile(path=path, content=current[path]) for path in sorted(current)]


def validate_change_bundle(
    files: Sequence[ProjectFile],
    bundle: ChangeBundle,
    *,
    before_capabilities: Sequence[ProjectCapability] | None = None,
    capability_extractor: ProjectCapabilityExtractor | None = None,
) -> ChangeBundleValidation:
    before = list(before_capabilities) if before_capabilities is not None else (
        capability_extractor or ProjectCapabilityExtractor()
    ).extract(files)
    try:
        after_files = apply_bundle_to_snapshot(files, bundle)
    except ChangeBundleError:
        raise
    extractor = capability_extractor or ProjectCapabilityExtractor()
    after = extractor.extract(after_files)
    violations = find_preserve_violations(bundle.preserves, before, after)
    if violations:
        raise ChangeBundleError(
            "preserve_violation",
            "变更会删除必须保留的既有能力",
            violations,
        )
    changed_files = [change.path for change in bundle.changes]
    return ChangeBundleValidation(
        before_fingerprint=snapshot_fingerprint(files),
        after_fingerprint=snapshot_fingerprint(after_files),
        before_capabilities=before,
        after_capabilities=after,
        changed_files=changed_files,
        diff_summary=[f"{change.operation}: {change.path}" for change in bundle.changes],
    )
