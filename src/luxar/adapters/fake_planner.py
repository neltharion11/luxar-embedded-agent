from __future__ import annotations

from luxar.domain.plans import ExecutionPlan
from luxar.domain.requirements import FirmwareRequirement


class FakePlanner:
    def __init__(self, plan: ExecutionPlan) -> None:
        self.plan = plan
        self.calls: list[FirmwareRequirement] = []

    def create_plan(
        self,
        requirement: FirmwareRequirement,
    ) -> ExecutionPlan:
        self.calls.append(requirement)
        return self.plan