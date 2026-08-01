from __future__ import annotations

from typing import Protocol

from luxar.domain.plans import ExecutionPlan
from luxar.domain.requirements import FirmwareRequirement


class Planner(Protocol):
    def create_plan(
        self,
        requirement: FirmwareRequirement,
    ) -> ExecutionPlan:
        ...