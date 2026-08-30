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
    # V2 持续 Agent：Session 跨消息稳定，client_turn_id 用于请求幂等。
    session_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    client_turn_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )

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

    @field_validator("session_id", "client_turn_id")
    @classmethod
    def normalize_continuous_agent_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("持续 Agent 标识不能为空")
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


class WebSerialOpenRequest(BaseModel):
    """Validated settings for one process-local interactive serial session."""

    model_config = ConfigDict(extra="forbid", strict=True)

    port: str = Field(min_length=1, max_length=80)
    baud_rate: int = Field(default=115_200, ge=300, le=4_000_000)
    data_bits: Literal[5, 6, 7, 8] = 8
    parity: Literal["none", "even", "odd", "mark", "space"] = "none"
    stop_bits: Literal[1.0, 1.5, 2.0] = 1.0

    @field_validator("port")
    @classmethod
    def normalize_port(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("串口不能为空")
        return normalized


class WebSerialWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["text", "hex"] = "text"
    payload: str = Field(min_length=1, max_length=32_768)
    line_ending: Literal["none", "lf", "crlf"] = "none"


class WebDriverPublishRequest(BaseModel):
    """Publish selected current-project files as one immutable public driver."""

    model_config = ConfigDict(extra="forbid", strict=True)

    root_index: int = Field(default=0, ge=0)
    driver_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$",
    )
    name: str = Field(min_length=1, max_length=160)
    vendor: str = Field(default="", max_length=120)
    hardware: str = Field(min_length=1, max_length=160)
    protocols: list[str] = Field(min_length=1, max_length=16)
    targets: list[str] = Field(default_factory=list, max_length=32)
    description: str = Field(default="", max_length=2_000)
    file_paths: list[str] = Field(min_length=1, max_length=64)


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


class WebModelEndpointUpdate(BaseModel):
    """Editable endpoint fields; omitted API keys preserve the stored secret."""

    model_config = ConfigDict(extra="forbid", strict=True)

    provider: Literal["deepseek", "openai", "local"]
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    base_url: str = Field(default="", max_length=1000)
    model: str = Field(default="", max_length=300)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    thinking_enabled: bool = False
    thinking_effort: Literal["low", "high", "max"] = "high"
    context_window_tokens: int | None = Field(
        default=None,
        ge=4_096,
        le=2_000_000,
    )


class WebEmbeddingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["local_hash", "api"] = "local_hash"
    provider: Literal["openai", "local"] = "local"
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    base_url: str = Field(default="", max_length=1000)
    model: str = Field(default="", max_length=300)
    dimensions: int = Field(default=384, ge=32, le=4096)
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)


class WebModelConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    conversation: WebModelEndpointUpdate
    vision_mode: Literal["inherit", "separate", "python"] = "python"
    vision: WebModelEndpointUpdate | None = None
    embedding: WebEmbeddingUpdate | None = None


class WebApprovalDecision(BaseModel):
    """浏览器对计划、澄清、知识写入或烧录中断的回复。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["approve", "reject"]
    feedback: str = Field(default="", max_length=4000)
    selected_option: str | None = Field(default=None, max_length=300)
    root_index: int = Field(default=0, ge=0)


class WebSteeringRequest(BaseModel):
    """A message queued for the currently running continuous-Agent Turn."""

    model_config = ConfigDict(extra="forbid", strict=True)

    message: str = Field(min_length=1, max_length=8_000)
    client_steering_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    session_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    root_index: int = Field(default=0, ge=0)

    @field_validator("message")
    @classmethod
    def normalize_steering_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("steering 消息不能为空")
        return normalized


class WebCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
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


class WebKnowledgePdfImport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    relative_path: str = Field(min_length=1, max_length=1000)
    title: str | None = Field(default=None, max_length=300)
    root_index: int = Field(default=0, ge=0)


class WebAgentInteractionRequest(BaseModel):
    """独立交互入口；kind 决定是否允许 Agent 改变目标或计划。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["question", "change_objective", "change_plan"]
    message: str = Field(min_length=1, max_length=8_000)
    target_id: str | None = Field(default=None, max_length=240)
    root_index: int = Field(default=0, ge=0)

    @field_validator("message")
    @classmethod
    def normalize_agent_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("交互消息不能为空")
        return normalized


class WebAgentObjective(BaseModel):
    objective_id: str
    title: str
    description: str
    status: str
    priority: int
    acceptance_criteria: list[str]
    constraints: list[str]
    revision: int


class WebAgentChange(BaseModel):
    operation: str
    capability_id: str
    rationale: str = ""


class WebAgentTask(BaseModel):
    task_id: str
    parent_id: str | None = None
    kind: str
    title: str
    description: str
    depends_on: list[str]
    status: str
    attempts: int
    max_attempts: int
    requires_approval: bool
    allowed_tools: list[str]
    acceptance_criteria: list[str]


class WebAgentCapability(BaseModel):
    capability_id: str
    kind: str
    parameters: dict[str, object]
    status: str
    owners: list[str]
    evidence_ids: list[str]
    source_kind: str
    confidence: float


class WebAgentAcceptance(BaseModel):
    criterion_id: str
    description: str
    verification_kind: str
    status: str
    required_evidence: list[str]
    evidence_ids: list[str]


class WebAgentEvidence(BaseModel):
    evidence_id: str
    kind: str
    accepted_by: list[str] = Field(default_factory=list)


class WebAgentInteraction(BaseModel):
    interaction_id: str
    objective_id: str | None = None
    kind: str
    payload: dict[str, object]
    queued: bool = False


class WebAgentRecovery(BaseModel):
    task_id: str
    category: str
    message: str
    attempt: int
    repeated: bool


class WebAgentSnapshot(BaseModel):
    project: str
    root_index: int
    revision: int
    status: str
    objective: WebAgentObjective
    changes: list[WebAgentChange]
    tasks: list[WebAgentTask]
    capabilities: list[WebAgentCapability]
    acceptance: list[WebAgentAcceptance]
    evidence: list[WebAgentEvidence]
    interactions: list[WebAgentInteraction]
    recovery: list[WebAgentRecovery]
    trace: list[str] = Field(default_factory=list)
    current_task_id: str | None = None
    acceptance_passed: bool = False
    build_verified: bool = False
    hardware_function_verified: bool = False
    blocked_reason: str | None = None
    workflow_family: str = "supervisor_firmware"
    task_mode: str = "firmware"
    thread_id: str | None = None
    supports_interactions: bool = True
    knowledge_task: dict[str, object] | None = None
    knowledge_result: dict[str, object] | None = None
