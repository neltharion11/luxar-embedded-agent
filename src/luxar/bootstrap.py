"""应用组合根：创建共享 DeepSeek Client，并装配一次 Graph 调用所需的 RuntimeContext。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from luxar.adapters.espidf_cli import EspIdfCliAdapter
from luxar.adapters.espidf_device import EspIdfDeviceAdapter
from luxar.adapters.espidf_project import EspIdfProjectAdapter
from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.adapters.deepseek.client import (
    DeepSeekJsonClient,
    JsonCompletionClient,
)
from luxar.adapters.deepseek.log_analyst import DeepSeekLogAnalyst
from luxar.adapters.deepseek.planner import DeepSeekPlanner
from luxar.adapters.deepseek.repair_planner import DeepSeekRepairPlanner
from luxar.adapters.deepseek.requirement_parser import (
    DeepSeekRequirementParser,
)
from luxar.adapters.deepseek.settings import DeepSeekSettings
from luxar.application.context import RuntimeContext
from luxar.ports.espidf import EspIdfPort
from luxar.ports.espidf_device import EspIdfFlashPort, EspIdfMonitorPort
from luxar.ports.espidf_project import EspIdfProjectPort
from luxar.ports.log_analyst import LogAnalystPort
from luxar.ports.workspace import WorkspacePort


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
    settings: DeepSeekSettings | None = None,
    client: JsonCompletionClient | None = None,
    allow_dependency_downloads: bool = False,
    idf_command: Sequence[str] = ("idf.py",),
) -> RuntimeContext:
    # 正式运行时自动读取环境变量；测试可以传入无真实密钥的 Settings。
    if settings is None:
        settings = DeepSeekSettings()

    # 正式运行时创建真实 Client；测试可以注入 FakeJsonCompletionClient。
    if client is None:
        client = DeepSeekJsonClient(settings)

    if espidf is None:
        espidf = EspIdfCliAdapter(
            idf_command=idf_command,
            allow_dependency_downloads=allow_dependency_downloads,
        )

    if workspace is None:
        workspace = LocalWorkspaceAdapter()

    if project_creator is None:
        # 与构建 Adapter 共享同一个经过校验的 idf.py 启动器。
        project_creator = EspIdfProjectAdapter(
            idf_command=idf_command,
        )

    if flasher is None or monitor is None:
        # 烧录与监控共享同一个无状态设备 Adapter。
        device_adapter = EspIdfDeviceAdapter(
            idf_command=idf_command,
        )
        if flasher is None:
            flasher = device_adapter
        if monitor is None:
            monitor = device_adapter

    if checkpointer is None:
        # 进程内持久化：足以支持 interrupt() 与同进程恢复；
        # 跨进程的 SQLite 持久化属于后续切片。
        checkpointer = InMemorySaver()

    requirement_parser = DeepSeekRequirementParser(
        client=client,
        model=settings.fast_model,
    )
    planner = DeepSeekPlanner(
        client=client,
        model=settings.fast_model,
    )
    repair_planner = DeepSeekRepairPlanner(
        client=client,
        model=settings.repair_model,
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
    )
