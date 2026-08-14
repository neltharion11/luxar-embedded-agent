"""构建证据领域模型：保存编译器产生的真实结果、诊断位置及一致性规则。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BuildDiagnostic(BaseModel):
    file: str | None = None
    # 行列号可能缺失；一旦存在就必须从 1 开始，ge 是 greater than or equal。
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    severity: Literal["warning", "error"]
    code: str | None = None
    message: str = Field(min_length=1)


class BuildEvidence(BaseModel):
    success: bool
    command: list[str] = Field(min_length=1)
    return_code: int
    stdout_summary: str = ""
    stderr_summary: str = ""
    error_category: Literal[
        "environment",
        "dependency",
        "source",
        "linker",
        "timeout",
        "unknown",
    ] | None = None
    # 每份构建证据拥有独立的诊断列表。
    diagnostics: list[BuildDiagnostic] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result_consistency(self) -> BuildEvidence:
        # mode="after" 表示所有字段已分别完成类型验证，再检查字段之间是否互相矛盾。
        if self.success and self.return_code != 0:
            raise ValueError(
                "successful build evidence must have return_code 0"
            )

        if not self.success and self.return_code == 0:
            raise ValueError(
                "failed build evidence cannot have return_code 0"
            )

        if self.success and self.error_category is not None:
            raise ValueError(
                "successful build evidence cannot have an error category"
            )

        # after 验证器成功时必须返回当前模型对象，Pydantic 才能完成创建。
        return self
