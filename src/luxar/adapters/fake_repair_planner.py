"""修复规划 Fake：返回预设 RepairPlan，并完整记录生成修复所依据的输入。"""

from __future__ import annotations

from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan
from luxar.domain.repairs import ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


class FakeRepairPlanner:
    def __init__(self, repair: RepairPlan) -> None:
        self.repair = repair
        # 嵌套 tuple 类型精确描述每条调用记录中四个位置的类型。
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
                # 复制文件列表，避免调用后修改原列表导致历史记录跟着变化。
                list(files),
            )
        )

        return self.repair
