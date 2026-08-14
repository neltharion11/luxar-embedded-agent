"""LangGraph 状态定义：描述一次任务运行中会持续积累和变化的业务数据。"""

from __future__ import annotations

from typing import Literal, TypedDict

from luxar.domain.errors import WorkflowError
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan
from luxar.domain.requirements import FirmwareRequirement
from luxar.domain.repairs import RepairPlan


WorkflowStatus = Literal[
    # 状态值使用有限字符串集合，避免节点写入拼错或未知的阶段名称。
    "requirement_analyzed",
    "needs_clarification",
    "planned",
    "building",
    "retrying",
    "repaired",
    "completed",
    "failed",
]


class WorkflowState(TypedDict, total=False):
    # TypedDict 只描述字典应有哪些键和值类型，运行时仍是普通 dict。
    # total=False 表示节点可以逐步补充字段，不要求初始 State 一次提供全部键。
    task_text: str
    requirement: FirmwareRequirement
    plan: ExecutionPlan
    build_evidence: BuildEvidence
    error: WorkflowError
    attempts: int
    max_attempts: int
    status: WorkflowStatus
    trace: list[str]
    repair_plan: RepairPlan
    changed_files: list[str]
