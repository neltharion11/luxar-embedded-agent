"""Web 展示合同：验证浏览器输入，并定义不会泄漏内部对象的响应形状。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_TARGET_CHIP_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class WebTaskRequest(BaseModel):
    """浏览器启动一次工作流时允许提交的数据。

    项目根、串口与芯片都由页面选择后随任务提交；服务器按白名单
    与正则严格校验，任意值永远不会到达 idf.py。
    """

    # strict=True 防止 1、"true" 等值被悄悄转换成下载授权。
    model_config = ConfigDict(extra="forbid", strict=True)

    message: str
    stream: Literal[True] = True
    max_attempts: int = Field(default=3, ge=1, le=10)
    allow_dependency_downloads: bool = False
    # 项目根索引：指向服务器配置的项目根列表；缺省用第 0 个。
    root_index: int = Field(default=0, ge=0)
    # 页面选择的串口：服务器仍会校验平台模式与发现列表成员资格。
    serial_port: str | None = None
    # 页面选择的芯片：仅接受小写标识符。
    target_chip: str | None = None

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("固件需求不能为空")
        return normalized

    @field_validator("serial_port")
    @classmethod
    def normalize_serial_port(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("串口不能为空")
        return normalized

    @field_validator("target_chip")
    @classmethod
    def normalize_target_chip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("芯片不能为空")
        if not _TARGET_CHIP_RE.fullmatch(normalized):
            raise ValueError("芯片名必须是 esp32 之类的小写标识符")
        return normalized


class WebProject(BaseModel):
    """项目列表只暴露逻辑名称和平台，不暴露主机路径。"""

    name: str
    platform: Literal["espidf"] = "espidf"
    root_index: int = Field(default=0, ge=0)


class WebProjectRoot(BaseModel):
    """项目根只暴露索引与展示标签，不暴露主机路径。"""

    index: int = Field(ge=0)
    label: str


class WebProjectList(BaseModel):
    roots: list[WebProjectRoot]
    projects: list[WebProject]


class WebSerialPort(BaseModel):
    """串口只暴露名称、描述与硬件 ID，全部经过服务器脱敏。"""

    name: str
    description: str = ""
    hardware_id: str = ""


class WebSerialPortList(BaseModel):
    ports: list[WebSerialPort]


class WebHealth(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["luxar-langgraph"] = "luxar-langgraph"


class WebApprovalDecision(BaseModel):
    """浏览器对烧录审批请求的唯一合法回复。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["approve", "reject"]
    root_index: int = Field(default=0, ge=0)
