"""源码修复领域模型：验证项目文件快照、完整文件替换和安全相对路径。"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_project_relative_path(value: str) -> str:
    """把不同平台路径统一为安全的项目相对路径；这里只分析字符串，不访问磁盘。"""
    stripped = value.strip()

    if not stripped:
        raise ValueError("project file path cannot be empty")

    # 先统一分隔符，再同时用 POSIX/Windows 规则检查，避免跨平台绝对路径绕过。
    normalized = stripped.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(stripped)

    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        # C:relative.txt 虽不一定被视为绝对路径，但仍带盘符，因此也必须拒绝。
        or windows_path.drive
    ):
        raise ValueError("project file path must be relative")

    if ".." in posix_path.parts:
        raise ValueError("project file path cannot leave the project directory")

    if posix_path == PurePosixPath("."):
        raise ValueError("project file path must identify a file")

    return posix_path.as_posix()


class ProjectFile(BaseModel):
    path: str
    content: str
    #: 磁盘原始字节的 SHA-256（小写 hex）。供上层（workspace.read_project /
    #: apply_change_bundle 的 expected_sha256）按 executor 的同一字节基准校验。
    #: 空串表示调用方未提供（仅测试替身/旧路径会出现）。
    sha256: str = ""

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        # field_validator 只负责 path 这一个字段，并把规范化后的值写回模型。
        return normalize_project_relative_path(value)


class FileReplacement(BaseModel):
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_project_relative_path(value)


class RepairPlan(BaseModel):
    diagnosis: str = Field(min_length=1)
    replacements: list[FileReplacement] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_replacement_paths(self) -> RepairPlan:
        # 先收集所有目标路径，再用 set 去重；长度变化就说明同一文件被替换多次。
        paths = [
            replacement.path
            for replacement in self.replacements
        ]

        if len(paths) != len(set(paths)):
            raise ValueError(
                "repair plan cannot replace the same file more than once"
            )

        return self
