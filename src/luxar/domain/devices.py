"""设备领域模型：串口描述、烧录证据和烧录审批请求。

烧录与监控的事实只能由真实工具产生；这些模型用 Pydantic 验证
工具返回的结构，并约束审批请求中允许出现的内容。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SerialPortInfo(BaseModel):
    # 串口名不允许空白字符；平台模式校验由 Adapter 负责。
    name: str = Field(min_length=1)
    description: str = ""
    hardware_id: str = ""

    @model_validator(mode="after")
    def validate_no_whitespace_name(self) -> SerialPortInfo:
        if any(character.isspace() for character in self.name):
            raise ValueError("serial port name cannot contain whitespace")

        return self


class FlashEvidence(BaseModel):
    success: bool
    # 逻辑命令允许携带用户显式选择的串口名，但绝不携带绝对工具路径。
    command: list[str] = Field(min_length=1)
    return_code: int
    port: str
    stdout_summary: str = ""
    stderr_summary: str = ""
    error_category: Literal[
        "serial",
        "timeout",
        "environment",
        "unknown",
    ] | None = None

    @model_validator(mode="after")
    def validate_result_consistency(self) -> FlashEvidence:
        # 与 BuildEvidence 相同：成功与返回码、错误类别必须互相一致。
        if self.success and self.return_code != 0:
            raise ValueError(
                "successful flash evidence must have return_code 0"
            )

        if not self.success and self.return_code == 0:
            raise ValueError(
                "failed flash evidence cannot have return_code 0"
            )

        if self.success and self.error_category is not None:
            raise ValueError(
                "successful flash evidence cannot have an error category"
            )

        if any(character.isspace() for character in self.port):
            raise ValueError("flash evidence port cannot contain whitespace")

        return self


class ApprovalRequest(BaseModel):
    # 审批请求只携带受控展示字段：无绝对路径、无命令文本、无密钥。
    project_name: str = Field(min_length=1)
    port: str = Field(min_length=1)
    target_chip: str | None = None
    summary: str = Field(min_length=1)
    step_description: str = Field(min_length=1)
    attempts: int = Field(ge=0)
