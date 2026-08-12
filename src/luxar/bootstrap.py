"""应用组合根：创建共享 DeepSeek Client，并装配一次 Graph 调用所需的 RuntimeContext。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from luxar.adapters.espidf_cli import EspIdfCliAdapter
from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.adapters.deepseek.client import (
    DeepSeekJsonClient,
    JsonCompletionClient,
)
from luxar.adapters.deepseek.planner import DeepSeekPlanner
from luxar.adapters.deepseek.repair_planner import DeepSeekRepairPlanner
from luxar.adapters.deepseek.requirement_parser import (
    DeepSeekRequirementParser,
)
from luxar.adapters.deepseek.settings import DeepSeekSettings
from luxar.application.context import RuntimeContext
from luxar.ports.espidf import EspIdfPort
from luxar.ports.workspace import WorkspacePort


def build_deepseek_runtime_context(
    *,
    project_path: Path,
    espidf: EspIdfPort | None = None,
    workspace: WorkspacePort | None = None,
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

    return RuntimeContext(
        requirement_parser=requirement_parser,
        planner=planner,
        repair_planner=repair_planner,
        espidf=espidf,
        workspace=workspace,
        project_path=project_path,
    )
