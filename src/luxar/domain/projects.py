"""项目创建证据领域模型：保存 idf.py create-project 产生的真实结果。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# 项目目录名只能是单一相对段：拒绝分隔符、盘符、父目录跳转和隐藏名称。
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ProjectEvidence(BaseModel):
    success: bool
    # 逻辑命令永远不包含绝对工具路径或父目录路径。
    command: list[str] = Field(min_length=1)
    return_code: int
    # 只保留项目相对目录名，绝不泄露磁盘绝对路径。
    created_dir: str | None = None
    already_existed: bool = False
    stdout_summary: str = ""
    stderr_summary: str = ""
    error_category: Literal[
        "environment",
        "process",
        "invalid_project",
        "timeout",
    ] | None = None

    @field_validator("created_dir")
    @classmethod
    def validate_created_dir(cls, value: str | None) -> str | None:
        if value is None:
            return None

        if not _PROJECT_NAME_RE.fullmatch(value):
            raise ValueError(
                "created_dir must be a single relative project name"
            )

        return value

    @model_validator(mode="after")
    def validate_result_consistency(self) -> ProjectEvidence:
        # 与 BuildEvidence 相同的规则：成功与返回码、错误类别必须互相一致。
        if self.success and self.return_code != 0:
            raise ValueError(
                "successful project evidence must have return_code 0"
            )

        if not self.success and self.return_code == 0:
            raise ValueError(
                "failed project evidence cannot have return_code 0"
            )

        if self.success and self.error_category is not None:
            raise ValueError(
                "successful project evidence cannot have an error category"
            )

        if self.already_existed and not self.success:
            raise ValueError(
                "already_existed project evidence must be successful"
            )

        return self
