"""Web 展示合同：验证浏览器输入，并定义不会泄漏内部对象的响应形状。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_TARGET_CHIP_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class WebTaskRequest(BaseModel):
    """浏览器启动一次工作流时允许提交的数据。

    项目根与串口随任务提交；芯片通常从不可变项目配置读取。
    保留 target_chip 仅用于兼容旧客户端，服务端会拒绝与项目冲突的值。
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
    # 旧客户端兼容字段：仅接受小写标识符，且不得改变项目固定芯片。
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
    target_chip: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class WebProjectRoot(BaseModel):
    """项目根只暴露索引与展示标签，不暴露主机路径。"""

    index: int = Field(ge=0)
    label: str


class WebProjectList(BaseModel):
    roots: list[WebProjectRoot]
    projects: list[WebProject]


class WebProjectSelection(BaseModel):
    """本机目录选择器的结果；取消选择时 project 为 None。"""

    project: WebProject | None = None


class WebProjectSelectionRequest(BaseModel):
    """Select an existing project and bind its immutable target chip."""

    model_config = ConfigDict(extra="forbid", strict=True)

    target_chip: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class WebProjectCreateRequest(BaseModel):
    """在服务器已配置根目录中创建一个 ESP-IDF 项目。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    target_chip: str = Field(
        default="esp32",
        min_length=1,
        max_length=40,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    root_index: int = Field(default=0, ge=0)


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


class WebEspIdfToolchain(BaseModel):
    """启动时探测到的 ESP-IDF 工具链状态。"""

    available: bool
    source: Literal[
        "none",
        "environment",
        "configured",
        "installer",
        "path",
        "search",
    ]
    version: str | None = None
    idf_path: str | None = None
    message: str


class WebApprovalDecision(BaseModel):
    """浏览器对烧录审批请求的唯一合法回复。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["approve", "reject"]
    root_index: int = Field(default=0, ge=0)


class WebMemoryUpsert(BaseModel):
    """Explicit structured project memory; arbitrary SQL/query text is absent."""

    model_config = ConfigDict(extra="forbid", strict=True)

    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_.-]+$")
    memory_type: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_.-]+$",
    )
    value: dict[str, object]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    root_index: int = Field(default=0, ge=0)


class WebKnowledgeIngest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_uri: str = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    metadata: dict[str, object] = Field(default_factory=dict)
    root_index: int = Field(default=0, ge=0)


class WebKnowledgeSearch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=6, ge=1, le=20)
    root_index: int = Field(default=0, ge=0)
