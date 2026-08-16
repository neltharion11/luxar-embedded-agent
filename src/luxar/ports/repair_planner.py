"""修复规划 Port：规定根据需求、计划、构建证据和源码生成 RepairPlan 的能力。"""

from __future__ import annotations

from typing import Protocol

from luxar.domain.devices import DeviceDiagnosis
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan
from luxar.domain.repairs import ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


class RepairPlanner(Protocol):
    # 模型只能提出结构化修复计划，不能在这个接口中直接读写文件或宣称构建成功。
    def create_repair(
        self,
        requirement: FirmwareRequirement,
        plan: ExecutionPlan,
        evidence: BuildEvidence,
        files: list[ProjectFile],
        # S4：设备回路修复附带日志诊断；构建失败修复传 None。
        device_diagnosis: DeviceDiagnosis | None = None,
    ) -> RepairPlan:
        ...
