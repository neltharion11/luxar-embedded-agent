from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from pydantic import BaseModel, Field, field_validator, model_validator


def normalize_project_relative_path(value: str) -> str:
    stripped = value.strip()

    if not stripped:
        raise ValueError("project file path cannot be empty")

    normalized = stripped.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(stripped)

    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
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

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
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
        paths = [
            replacement.path
            for replacement in self.replacements
        ]

        if len(paths) != len(set(paths)):
            raise ValueError(
                "repair plan cannot replace the same file more than once"
            )

        return self