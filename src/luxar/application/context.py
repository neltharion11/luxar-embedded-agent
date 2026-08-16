"""运行时上下文：集中保存一次 Graph 调用所需的外部能力和项目路径。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from luxar.ports.espidf import EspIdfPort
from luxar.ports.espidf_project import EspIdfProjectPort
from luxar.ports.planner import Planner
from luxar.ports.requirement_parser import RequirementParser
from luxar.ports.repair_planner import RepairPlanner
from luxar.ports.workspace import WorkspacePort


@dataclass(frozen=True)
class RuntimeContext:
    # frozen=True 防止工作流运行中意外替换依赖；这些对象由启动代码统一注入。
    # Context 不进入 State/checkpoint，因此 API 客户端、密钥和文件工具不会被持久化。
    requirement_parser: RequirementParser
    planner: Planner
    espidf: EspIdfPort
    project_path: Path
    repair_planner: RepairPlanner
    workspace: WorkspacePort
    project_creator: EspIdfProjectPort
    # 目标芯片的可选显式配置；为 None 时创建节点回退到 requirement.target。
    target_chip: str | None
