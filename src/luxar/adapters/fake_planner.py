"""计划生成 Fake：测试时返回预设执行计划，并记录收到的固件需求。"""

from __future__ import annotations

from luxar.domain.plans import ExecutionPlan
from luxar.domain.requirements import FirmwareRequirement


class FakePlanner:
    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan
        # 构造函数注入结果，让每个测试可以独立控制 Planner 的行为。
        self.calls: list[FirmwareRequirement] = []

    def create_plan(
        self,
        requirement: FirmwareRequirement,
    ) -> ExecutionPlan:
        self.calls.append(requirement)
        return self.plan
