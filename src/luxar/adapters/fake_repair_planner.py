from __future__ import annotations

from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan
from luxar.domain.repairs import ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


class FakeRepairPlanner:
    def __init__(self, repair: RepairPlan) -> None:
        self.repair = repair
        self.calls: list[
            tuple[
                FirmwareRequirement,
                ExecutionPlan,
                BuildEvidence,
                list[ProjectFile],
            ]
        ] = []

    def create_repair(
        self,
        requirement: FirmwareRequirement,
        plan: ExecutionPlan,
        evidence: BuildEvidence,
        files: list[ProjectFile],
    ) -> RepairPlan:
        self.calls.append(
            (
                requirement,
                plan,
                evidence,
                list(files),
            )
        )

        return self.repair