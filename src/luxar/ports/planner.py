"""计划生成 Port：规定“结构化需求转执行计划”能力的最小接口。"""

from __future__ import annotations

from typing import Protocol

from luxar.domain.plans import ExecutionPlan
from luxar.domain.requirements import FirmwareRequirement


class Planner(Protocol):
    # 省略号表示这里只声明合同，不在 Port 中提供模型或规则实现。
    def create_plan(
        self,
        requirement: FirmwareRequirement,
    ) -> ExecutionPlan:
        ...
