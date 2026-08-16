"""Web 展示合同：验证浏览器输入，并定义不会泄漏内部对象的响应形状。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebTaskRequest(BaseModel):
    """浏览器启动一次工作流时唯一允许提交的数据。"""

    # strict=True 防止 1、"true" 等值被悄悄转换成下载授权。
    model_config = ConfigDict(extra="forbid", strict=True)

    message: str
    stream: Literal[True] = True
    max_attempts: int = Field(default=3, ge=1, le=10)
    allow_dependency_downloads: bool = False

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("固件需求不能为空")
        return normalized


class WebProject(BaseModel):
    """项目列表只暴露逻辑名称和平台，不暴露主机路径。"""

    name: str
    platform: Literal["espidf"] = "espidf"


class WebProjectList(BaseModel):
    projects: list[WebProject]


class WebHealth(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["luxar-langgraph"] = "luxar-langgraph"


class WebApprovalDecision(BaseModel):
    """浏览器对烧录审批请求的唯一合法回复。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["approve", "reject"]
