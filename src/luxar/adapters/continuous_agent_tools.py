"""Typed wrappers that expose existing LUXAR ports to the continuous Agent."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from luxar.application.project_analysis import analyze_current_project
from luxar.application.tool_registry import ToolRegistry
from luxar.database.persistence import PersistencePort
from luxar.domain.agent.code_changes import ChangeBundle, ChangeBundleError
from luxar.domain.continuous_agent.failures import ContinuousAgentFailure
from luxar.domain.continuous_agent.steps import AgentToolDescriptor
from luxar.domain.continuous_agent.tools import ToolResult
from luxar.ports.agent_tool import (
    AgentToolExecutionContext,
    ToolExecutionLedgerPort,
)
from luxar.ports.code_executor import CodeExecutorPort
from luxar.ports.espidf import EspIdfPort
from luxar.ports.espidf_device import EspIdfFlashPort, EspIdfMonitorPort
from luxar.ports.project_analyzer import ProjectAnalyzer
from luxar.ports.workspace import WorkspacePort
from luxar.ports.workspace_errors import WorkspaceError


class KnowledgeSearchPort(Protocol):
    def search(
        self,
        *,
        project_key: str,
        query: str,
        limit: int = 6,
    ) -> list[object]: ...


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _FlashArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    serial_port: str = Field(min_length=1, max_length=80)


class _MonitorArguments(_FlashArguments):
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class _ApplyChangeBundleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    bundle: ChangeBundle


class _KnowledgeSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=4_000)
    limit: int = Field(default=6, ge=1, le=20)


def _project_path(context: AgentToolExecutionContext) -> Path:
    if context.project_path is None:
        raise ValueError("Agent tool project path is unavailable")
    return context.project_path


class WorkspaceReadProjectTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="workspace.read_project",
        description="读取当前项目内受控的源码和 ESP-IDF 配置文件",
        input_schema=_NoArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self, workspace: WorkspacePort) -> None:
        self._workspace = workspace

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments
        files = self._workspace.read_project_files(_project_path(context))
        return ToolResult(
            success=True,
            output={
                "file_count": len(files),
                "files": [item.model_dump(mode="json") for item in files],
            },
            evidence_ids=[f"source:{item.path}" for item in files],
        )


class ProjectInspectTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="project.inspect",
        description="读取并分析当前项目结构、入口、已实现能力、缺口和风险",
        input_schema=_NoArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(
        self,
        *,
        workspace: WorkspacePort,
        analyzer: ProjectAnalyzer | None = None,
        persistence: PersistencePort | None = None,
        target_chip: str | None = None,
    ) -> None:
        self._workspace = workspace
        self._analyzer = analyzer
        self._persistence = persistence
        self._target_chip = target_chip

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments
        analysis = analyze_current_project(
            project_path=_project_path(context),
            target_chip=self._target_chip,
            workspace=self._workspace,
            analyzer=self._analyzer,
            persistence=self._persistence,
            project_key=context.project_key,
        )
        return ToolResult(
            success=True,
            output=analysis.model_dump(mode="json"),
            evidence_ids=[f"source:{path}" for path in analysis.evidence_paths],
        )


class WorkspaceApplyChangeBundleTool:
    input_model = _ApplyChangeBundleArguments
    descriptor = AgentToolDescriptor(
        name="workspace.apply_change_bundle",
        description=(
            "事务式应用经过路径白名单、快照哈希和既有能力保护约束的代码变更包"
        ),
        input_schema=_ApplyChangeBundleArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def __init__(self, executor: CodeExecutorPort) -> None:
        self._executor = executor

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _ApplyChangeBundleArguments)
        try:
            validation = self._executor.execute(
                _project_path(context),
                arguments.bundle,
            )
        except ChangeBundleError as error:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="validation",
                    code=error.category,
                    message=str(error),
                    retryable=error.category in {"conflict", "stale_snapshot"},
                    details={"paths": list(error.details)},
                ),
            )
        except WorkspaceError as error:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code=error.category,
                    message=error.message,
                    retryable=error.retryable,
                ),
            )
        return ToolResult(
            success=True,
            output=validation.model_dump(mode="json"),
            evidence_ids=[
                f"source:{path}" for path in validation.changed_files
            ],
        )


class KnowledgeSearchTool:
    input_model = _KnowledgeSearchArguments
    descriptor = AgentToolDescriptor(
        name="knowledge.search",
        description="在当前项目的外部知识库中检索相关资料和来源片段",
        input_schema=_KnowledgeSearchArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self, knowledge: KnowledgeSearchPort) -> None:
        self._knowledge = knowledge

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _KnowledgeSearchArguments)
        matches = self._knowledge.search(
            project_key=context.project_key,
            query=arguments.query,
            limit=arguments.limit,
        )
        payloads = [
            asdict(match)
            if hasattr(match, "__dataclass_fields__")
            else dict(match)  # type: ignore[arg-type]
            for match in matches
        ]
        return ToolResult(
            success=True,
            output={"matches": payloads, "count": len(payloads)},
            evidence_ids=[
                f"knowledge:{payload.get('document_id')}:{payload.get('ordinal')}"
                for payload in payloads
                if payload.get("document_id") is not None
            ],
        )


class EspIdfBuildTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="espidf.build",
        description="构建当前 ESP-IDF 工程并返回真实构建证据",
        input_schema=_NoArguments.model_json_schema(),
        read_only=False,
        requires_approval=False,
    )

    def __init__(self, builder: EspIdfPort) -> None:
        self._builder = builder

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments
        evidence = self._builder.build(_project_path(context))
        return ToolResult(
            success=evidence.success,
            output=evidence.model_dump(mode="json"),
            evidence_ids=["build:latest"] if evidence.success else [],
            failure=(
                None
                if evidence.success
                else ContinuousAgentFailure(
                    category="tool",
                    code=evidence.error_category or "build_failed",
                    message="ESP-IDF 构建未通过",
                    retryable=True,
                )
            ),
        )


class DeviceDiscoverTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="device.discover",
        description="发现当前主机上可安全使用的开发板串口",
        input_schema=_NoArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self, flasher: EspIdfFlashPort) -> None:
        self._flasher = flasher

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments, context
        ports = self._flasher.discover_serial_ports()
        return ToolResult(
            success=True,
            output={
                "ports": [item.model_dump(mode="json") for item in ports],
                "count": len(ports),
            },
        )


class DeviceFlashTool:
    input_model = _FlashArguments
    descriptor = AgentToolDescriptor(
        name="device.flash",
        description="把当前工程固件烧录到用户选择的串口",
        input_schema=_FlashArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def __init__(self, flasher: EspIdfFlashPort) -> None:
        self._flasher = flasher

    def validate_policy(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ContinuousAgentFailure | None:
        del context
        assert isinstance(arguments, _FlashArguments)
        ports = self._flasher.discover_serial_ports()
        if arguments.serial_port not in {item.name for item in ports}:
            return ContinuousAgentFailure(
                category="policy",
                code="serial_port_not_discovered",
                message="目标串口不在当前安全发现列表中",
                retryable=True,
                details={"serial_port": arguments.serial_port},
            )
        return None

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _FlashArguments)
        evidence = self._flasher.flash(
            _project_path(context),
            arguments.serial_port,
        )
        return ToolResult(
            success=evidence.success,
            output=evidence.model_dump(mode="json"),
            evidence_ids=["flash:latest"] if evidence.success else [],
            failure=(
                None
                if evidence.success
                else ContinuousAgentFailure(
                    category="tool",
                    code=evidence.error_category or "flash_failed",
                    message="设备烧录未通过",
                    retryable=True,
                )
            ),
        )


class DeviceMonitorTool:
    input_model = _MonitorArguments
    descriptor = AgentToolDescriptor(
        name="device.monitor",
        description="在受控时间内读取开发板串口日志并提取诊断",
        input_schema=_MonitorArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(
        self,
        monitor: EspIdfMonitorPort,
        *,
        port_discoverer: EspIdfFlashPort | None = None,
    ) -> None:
        self._monitor = monitor
        self._port_discoverer = port_discoverer

    def validate_policy(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ContinuousAgentFailure | None:
        del context
        assert isinstance(arguments, _MonitorArguments)
        if self._port_discoverer is None:
            return None
        ports = self._port_discoverer.discover_serial_ports()
        if arguments.serial_port not in {item.name for item in ports}:
            return ContinuousAgentFailure(
                category="policy",
                code="serial_port_not_discovered",
                message="目标串口不在当前安全发现列表中",
                retryable=True,
                details={"serial_port": arguments.serial_port},
            )
        return None

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _MonitorArguments)
        evidence = self._monitor.monitor(
            _project_path(context),
            arguments.serial_port,
            arguments.timeout_seconds,
        )
        return ToolResult(
            success=True,
            output=evidence.model_dump(mode="json"),
            evidence_ids=["monitor:latest"],
        )


def create_core_tool_registry(
    *,
    workspace: WorkspacePort | None = None,
    code_executor: CodeExecutorPort | None = None,
    knowledge: KnowledgeSearchPort | None = None,
    analyzer: ProjectAnalyzer | None = None,
    builder: EspIdfPort | None = None,
    flasher: EspIdfFlashPort | None = None,
    monitor: EspIdfMonitorPort | None = None,
    persistence: PersistencePort | None = None,
    ledger: ToolExecutionLedgerPort | None = None,
    target_chip: str | None = None,
) -> ToolRegistry:
    tools = []
    if workspace is not None:
        tools.extend(
            [
                WorkspaceReadProjectTool(workspace),
                ProjectInspectTool(
                    workspace=workspace,
                    analyzer=analyzer,
                    persistence=persistence,
                    target_chip=target_chip,
                ),
            ]
        )
    if code_executor is not None:
        tools.append(WorkspaceApplyChangeBundleTool(code_executor))
    if knowledge is not None:
        tools.append(KnowledgeSearchTool(knowledge))
    if builder is not None:
        tools.append(EspIdfBuildTool(builder))
    if flasher is not None:
        tools.extend([DeviceDiscoverTool(flasher), DeviceFlashTool(flasher)])
    if monitor is not None:
        tools.append(
            DeviceMonitorTool(monitor, port_discoverer=flasher)
        )
    return ToolRegistry(tools, ledger=ledger)


__all__ = [
    "DeviceDiscoverTool",
    "DeviceFlashTool",
    "DeviceMonitorTool",
    "EspIdfBuildTool",
    "ProjectInspectTool",
    "KnowledgeSearchTool",
    "WorkspaceApplyChangeBundleTool",
    "WorkspaceReadProjectTool",
    "create_core_tool_registry",
]
