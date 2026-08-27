"""应用组合根：创建共享 DeepSeek Client，并装配一次 Graph 调用所需的 RuntimeContext。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from luxar.checkpoint_serde import create_checkpoint_serializer
from luxar.adapters.espidf_cli import EspIdfCliAdapter
from luxar.adapters.espidf_device import EspIdfDeviceAdapter
from luxar.adapters.espidf_project import EspIdfProjectAdapter
from luxar.adapters.espidf_examples import LocalEspIdfExampleLibrary
from luxar.adapters.espidf_sdk_probe import EspIdfSdkProbe
from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.adapters.deepseek.client import (
    DeepSeekJsonClient,
    JsonCompletionClient,
)
from luxar.adapters.deepseek.log_analyst import DeepSeekLogAnalyst
from luxar.adapters.deepseek.planner import DeepSeekPlanner
from luxar.adapters.deepseek.project_analyzer import DeepSeekProjectAnalyzer
from luxar.adapters.deepseek.firmware_editor import DeepSeekFirmwareEditor
from luxar.adapters.deepseek.agent_code_engineer import DeepSeekAgentCodeEngineer
from luxar.adapters.deepseek.agent_planner import DeepSeekAgentPlanner
from luxar.adapters.deepseek.repair_planner import DeepSeekRepairPlanner
from luxar.adapters.deepseek.requirement_parser import (
    DeepSeekRequirementParser,
)
from luxar.adapters.deepseek.knowledge_task_parser import DeepSeekKnowledgeTaskParser
from luxar.adapters.deepseek.document_analyzer import DeepSeekPdfTechnicalAnalyzer
from luxar.adapters.deepseek.knowledge_extractor import DeepSeekKnowledgeAtomExtractor
from luxar.adapters.deepseek.knowledge_answerer import DeepSeekKnowledgeAnswerer
from luxar.adapters.deepseek.settings import DeepSeekSettings
from luxar.model_config import ModelEndpoint, is_local_http_api
from luxar.document_reader import PdfDocumentReader
from luxar.application.context import RuntimeContext
from luxar.application.agent_state import AgentRuntimeContext
from luxar.application.specialized_context import SpecializedRuntimeContext
from luxar.adapters.transactional_code_executor import LocalChangeBundleExecutor
from luxar.domain.devices import SerialPortInfo
from luxar.database.persistence import PersistencePort
from luxar.knowledge import KnowledgeService, ProjectContextProvider
from luxar.sdk_knowledge import SdkExampleKnowledgeBase
from luxar.ports.espidf import EspIdfPort
from luxar.ports.espidf_device import EspIdfFlashPort, EspIdfMonitorPort
from luxar.ports.espidf_project import EspIdfProjectPort
from luxar.ports.log_analyst import LogAnalystPort
from luxar.ports.workspace import WorkspacePort
from luxar.ports.verification import (
    ComponentTestPort,
    FirmwareInspectorPort,
    ProtocolProbePort,
    RuntimeScenarioPort,
)


def _uses_local_model(settings: DeepSeekSettings | ModelEndpoint) -> bool:
    if isinstance(settings, ModelEndpoint):
        return settings.uses_local_execution
    return is_local_http_api(settings.base_url)


def build_deepseek_agent_runtime_context(
    *,
    project_path: Path,
    build_executor: EspIdfPort | None = None,
    workspace: WorkspacePort | None = None,
    flasher: EspIdfFlashPort | None = None,
    monitor: EspIdfMonitorPort | None = None,
    serial_port: str | None = None,
    monitor_timeout_seconds: int = 10,
    settings: DeepSeekSettings | ModelEndpoint | None = None,
    client: JsonCompletionClient | None = None,
    allow_dependency_downloads: bool = False,
    idf_command: Sequence[str] | None = None,
    component_tester: ComponentTestPort | None = None,
    firmware_inspector: FirmwareInspectorPort | None = None,
    protocol_probe: ProtocolProbePort | None = None,
    runtime_scenario_runner: RuntimeScenarioPort | None = None,
) -> AgentRuntimeContext:
    """Compose the production Supervisor execution and Code Engineer ports."""

    if settings is None:
        settings = DeepSeekSettings()
    if client is None:
        client = DeepSeekJsonClient(settings)
    resolved_idf_command = (
        idf_command if idf_command is not None else resolve_idf_command()
    )
    if build_executor is None:
        build_executor = EspIdfCliAdapter(
            idf_command=resolved_idf_command,
            allow_dependency_downloads=allow_dependency_downloads,
        )
    if (flasher is None or monitor is None) and serial_port is not None:
        device_adapter = EspIdfDeviceAdapter(idf_command=resolved_idf_command)
        if flasher is None:
            flasher = device_adapter
        if monitor is None:
            monitor = device_adapter
    local_workspace = (
        workspace if workspace is not None else LocalWorkspaceAdapter()
    )
    if not isinstance(local_workspace, LocalWorkspaceAdapter):
        # The transactional executor relies on the local adapter's bounded
        # file-size and path checks; a different read port remains available
        # to the Graph but cannot silently weaken write validation.
        transactional_workspace = LocalWorkspaceAdapter()
    else:
        transactional_workspace = local_workspace
    engineer = DeepSeekAgentCodeEngineer(
        client=client,
        model=(
            settings.resolved().repair_model
            if isinstance(settings, ModelEndpoint)
            else settings.repair_model
        ),
    )
    planner_model = (
        settings.resolved().fast_model
        if isinstance(settings, ModelEndpoint)
        else settings.fast_model
    )
    objective_planner = DeepSeekAgentPlanner(
        client=client,
        model=planner_model,
    )
    return AgentRuntimeContext(
        code_executor=LocalChangeBundleExecutor(transactional_workspace),
        code_engineer=engineer,
        sdk_probe=EspIdfSdkProbe(),
        objective_planner=objective_planner,
        project_path=project_path,
        schema_repair=engineer.repair_schema,
        build_executor=build_executor,
        flasher=flasher,
        workspace=local_workspace,
        monitor=monitor,
        serial_port=serial_port,
        monitor_timeout_seconds=monitor_timeout_seconds,
        component_tester=component_tester,
        firmware_inspector=firmware_inspector,
        protocol_probe=protocol_probe,
        runtime_scenario_runner=runtime_scenario_runner,
    )


def discover_serial_ports() -> list[SerialPortInfo]:
    """列出当前机器上符合平台模式的串口设备。"""

    return EspIdfDeviceAdapter().discover_serial_ports()


def resolve_idf_command() -> Sequence[str]:
    """解析默认的 idf.py 启动器,让未激活 shell 的服务进程也能工作。

    优先使用 IDF_PATH + IDF_PYTHON_ENV_PATH 的已知安装(激活脚本把
    idf.py 注册成 shell 函数而不是可执行文件,直接 spawn "idf.py"
    会在 Windows 上以 WinError 193 失败);否则回退 PATH 上的 idf.py。
    """

    import os
    import shutil

    idf_path = os.environ.get("IDF_PATH")
    python_env = os.environ.get("IDF_PYTHON_ENV_PATH")

    if idf_path and python_env:
        script = Path(idf_path) / "tools" / "idf.py"
        if os.name == "nt":
            python = (
                Path(python_env) / "Scripts" / "python.exe"
            )
        else:
            python = Path(python_env) / "bin" / "python"

        if script.is_file() and python.is_file():
            return (str(python), str(script))

    if shutil.which("idf.py") is not None:
        return ("idf.py",)

    return ("idf.py",)


def build_deepseek_specialized_runtime_context(
    *,
    project_path: Path,
    workspace: WorkspacePort | None = None,
    target_chip: str | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    settings: DeepSeekSettings | ModelEndpoint | None = None,
    client: JsonCompletionClient | None = None,
    persistence: PersistencePort | None = None,
    project_key: str | None = None,
    knowledge_service: KnowledgeService | None = None,
    document_reader: PdfDocumentReader | None = None,
) -> SpecializedRuntimeContext:
    """Compose only the capabilities used by dedicated read/knowledge flows."""

    if settings is None:
        settings = DeepSeekSettings()
    if client is None:
        client = DeepSeekJsonClient(settings)
    if workspace is None:
        workspace = LocalWorkspaceAdapter()
    if checkpointer is None:
        checkpointer = InMemorySaver(serde=create_checkpoint_serializer())
    resolved = settings.resolved() if isinstance(settings, ModelEndpoint) else settings
    return SpecializedRuntimeContext(
        project_path=project_path,
        workspace=workspace,
        checkpointer=checkpointer,
        target_chip=target_chip,
        project_analyzer=DeepSeekProjectAnalyzer(
            client=client,
            model=resolved.repair_model,
        ),
        persistence=persistence,
        project_key=project_key or project_path.name,
        knowledge_service=knowledge_service,
        knowledge_task_parser=DeepSeekKnowledgeTaskParser(
            client=client,
            model=resolved.fast_model,
        ),
        document_reader=document_reader or PdfDocumentReader(),
        document_analyzer=DeepSeekPdfTechnicalAnalyzer(
            client=client,
            model=resolved.fast_model,
            local_model=_uses_local_model(settings),
            max_workers=4,
        ),
        knowledge_extractor=DeepSeekKnowledgeAtomExtractor(
            client=client,
            model=resolved.fast_model,
        ),
        knowledge_answerer=DeepSeekKnowledgeAnswerer(
            client=client,
            model=resolved.repair_model,
        ),
    )


def build_deepseek_runtime_context(
    *,
    project_path: Path,
    espidf: EspIdfPort | None = None,
    workspace: WorkspacePort | None = None,
    project_creator: EspIdfProjectPort | None = None,
    flasher: EspIdfFlashPort | None = None,
    monitor: EspIdfMonitorPort | None = None,
    log_analyst: LogAnalystPort | None = None,
    target_chip: str | None = None,
    serial_port: str | None = None,
    monitor_timeout_seconds: int = 10,
    checkpointer: BaseCheckpointSaver | None = None,
    settings: DeepSeekSettings | ModelEndpoint | None = None,
    client: JsonCompletionClient | None = None,
    allow_dependency_downloads: bool = False,
    idf_command: Sequence[str] | None = None,
    idf_path: Path | None = None,
    persistence: PersistencePort | None = None,
    project_key: str | None = None,
    knowledge_service: KnowledgeService | None = None,
    sdk_example_knowledge: SdkExampleKnowledgeBase | None = None,
    document_reader: PdfDocumentReader | None = None,
) -> RuntimeContext:
    # 正式运行时自动读取环境变量；测试可以传入无真实密钥的 Settings。
    if settings is None:
        settings = DeepSeekSettings()

    # 正式运行时创建真实 Client；测试可以注入 FakeJsonCompletionClient。
    if client is None:
        client = DeepSeekJsonClient(settings)

    # 默认启动器按环境智能解析；显式传入时原样使用。
    resolved_idf_command = (
        idf_command
        if idf_command is not None
        else resolve_idf_command()
    )

    if espidf is None:
        espidf = EspIdfCliAdapter(
            idf_command=resolved_idf_command,
            allow_dependency_downloads=allow_dependency_downloads,
        )

    if workspace is None:
        workspace = LocalWorkspaceAdapter()

    if project_creator is None:
        # 与构建 Adapter 共享同一个经过校验的 idf.py 启动器。
        project_creator = EspIdfProjectAdapter(
            idf_command=resolved_idf_command,
        )

    if flasher is None or monitor is None:
        # 烧录与监控共享同一个无状态设备 Adapter。
        device_adapter = EspIdfDeviceAdapter(
            idf_command=resolved_idf_command,
        )
        if flasher is None:
            flasher = device_adapter
        if monitor is None:
            monitor = device_adapter

    if checkpointer is None:
        # 测试/显式无存储调用的进程内回退；正式 Web/CLI 运行会注入
        # SqliteSaver，以支持重启后的 interrupt() 恢复。
        checkpointer = InMemorySaver(serde=create_checkpoint_serializer())

    context_provider = None
    if persistence is not None:
        context_provider = ProjectContextProvider(
            persistence,
            project_key or project_path.name,
            knowledge_service,
        )

    requirement_parser = DeepSeekRequirementParser(
        client=client,
        model=settings.fast_model,
        context_provider=context_provider,
    )
    planner = DeepSeekPlanner(
        client=client,
        model=settings.fast_model,
    )
    repair_planner = DeepSeekRepairPlanner(
        client=client,
        model=settings.repair_model,
    )
    knowledge_task_parser = DeepSeekKnowledgeTaskParser(
        client=client, model=settings.fast_model
    )
    document_analyzer = DeepSeekPdfTechnicalAnalyzer(
        client=client,
        model=settings.fast_model,
        local_model=_uses_local_model(settings),
        max_workers=4,
    )
    knowledge_extractor = DeepSeekKnowledgeAtomExtractor(
        client=client, model=settings.fast_model
    )
    knowledge_answerer = DeepSeekKnowledgeAnswerer(
        client=client, model=settings.repair_model
    )
    project_analyzer = DeepSeekProjectAnalyzer(
        client=client,
        model=settings.repair_model,
    )
    firmware_editor = DeepSeekFirmwareEditor(
        client=client,
        model=settings.repair_model,
    )
    import os

    raw_idf_path = idf_path or (
        Path(value) if (value := os.environ.get("IDF_PATH")) else None
    )
    resolved_idf_path = (
        raw_idf_path.expanduser().resolve()
        if raw_idf_path is not None
        else None
    )
    example_library = (
        LocalEspIdfExampleLibrary(
            resolved_idf_path,
            knowledge=sdk_example_knowledge,
        )
        if resolved_idf_path is not None
        and (resolved_idf_path / "examples").is_dir()
        else None
    )
    if log_analyst is None:
        # 日志分析复用修复级模型，避免低能力模型漏报设备故障。
        log_analyst = DeepSeekLogAnalyst(
            client=client,
            model=settings.repair_model,
        )

    return RuntimeContext(
        requirement_parser=requirement_parser,
        planner=planner,
        repair_planner=repair_planner,
        espidf=espidf,
        workspace=workspace,
        project_creator=project_creator,
        flasher=flasher,
        monitor=monitor,
        log_analyst=log_analyst,
        project_path=project_path,
        target_chip=target_chip,
        serial_port=serial_port,
        monitor_timeout_seconds=monitor_timeout_seconds,
        checkpointer=checkpointer,
        project_analyzer=project_analyzer,
        firmware_editor=firmware_editor,
        example_library=example_library,
        persistence=persistence,
        project_key=project_key or project_path.name,
        knowledge_service=knowledge_service,
        knowledge_task_parser=knowledge_task_parser,
        document_reader=document_reader or PdfDocumentReader(),
        document_analyzer=document_analyzer,
        knowledge_extractor=knowledge_extractor,
        knowledge_answerer=knowledge_answerer,
        interactive_workflow=True,
    )
