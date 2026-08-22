"""计划生成 Fake：测试时返回预设执行计划，并记录收到的固件需求。"""

from __future__ import annotations

from luxar.domain.plans import ExecutionPlan
from luxar.domain.requirements import FirmwareRequirement
from luxar.domain.project_analysis import ProjectAnalysis


class FakePlanner:
    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan
        # 构造函数注入结果，让每个测试可以独立控制 Planner 的行为。
        self.calls: list[FirmwareRequirement] = []
        self.project_analyses: list[ProjectAnalysis | None] = []

    def create_plan(
        self,
        requirement: FirmwareRequirement,
        project_analysis: ProjectAnalysis | None = None,
    ) -> ExecutionPlan:
        self.calls.append(requirement)
        self.project_analyses.append(project_analysis)
        return self.plan
