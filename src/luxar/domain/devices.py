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


class DeviceLogDiagnostic(BaseModel):
    # 从受控采集的串口日志中结构化提取的故障模式。
    kind: Literal[
        "panic",
        "abort",
        "assert",
        "watchdog",
        "boot_loop",
        "error",
        "warning",
        "unknown",
    ]
    summary: str = Field(min_length=1)
    # 仅保留脱敏后的少量上下文行，避免把整段日志放进 State。
    lines: list[str] = Field(default_factory=list, max_length=8)


class MonitorEvidence(BaseModel):
    command: list[str] = Field(min_length=1)
    port: str
    capture_timeout_seconds: int = Field(ge=1)
    # 已经过 ANSI/绝对路径脱敏并限长的日志正文。
    captured_log: str = ""
    # True 表示采集窗口正常到期；False 表示进程自行退出。
    terminated_by_timeout: bool
    diagnostics: list[DeviceLogDiagnostic] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_port_has_no_whitespace(self) -> MonitorEvidence:
        if any(character.isspace() for character in self.port):
            raise ValueError("monitor evidence port cannot contain whitespace")

        return self


class DeviceDiagnosis(BaseModel):
    # 日志分析结论：healthy 与 repair_needed 互斥。
    healthy: bool
    repair_needed: bool
    summary: str = Field(min_length=1)
    findings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_diagnosis_consistency(self) -> DeviceDiagnosis:
        if self.healthy and self.repair_needed:
            raise ValueError(
                "healthy diagnosis cannot require a repair"
            )

        return self
