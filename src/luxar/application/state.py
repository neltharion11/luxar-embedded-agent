"""LangGraph 状态定义：描述一次任务运行中会持续积累和变化的业务数据。"""

from __future__ import annotations

from typing import Literal, TypedDict

from luxar.domain.devices import ApprovalRequest, FlashEvidence
from luxar.domain.errors import WorkflowError
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan
from luxar.domain.projects import ProjectEvidence
from luxar.domain.requirements import FirmwareRequirement
from luxar.domain.repairs import RepairPlan


WorkflowStatus = Literal[
    # 状态值使用有限字符串集合，避免节点写入拼错或未知的阶段名称。
    "requirement_analyzed",
    "needs_clarification",
    "planned",
    "project_created",
    "building",
    "flashing",
    "retrying",
    "repaired",
    "completed",
    "failed",
]


# 游标分发时记录当前正在执行的步骤类型；None 表示没有待分发步骤。
PendingStepKind = Literal[
    "create_project",
    "build_project",
    "flash_project",
    "monitor_project",
] | None


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
    # S1：计划游标。plan_index 指向下一个未执行的步骤。
    plan_index: int
    # S1：分发器写下的待执行步骤类型，供条件路由选择目标节点。
    pending_step_kind: PendingStepKind
    # S4 使用：记录触发修复的来源，决定重建成功后回到构建还是进入设备回路。
    repair_origin: Literal["build", "monitor"] | None
    # S2：项目创建步骤产生的真实工具证据。
    created_project: ProjectEvidence
    # S3：烧录证据、烧录尝试计数与审批状态。
    flash_evidence: FlashEvidence
    flash_attempts: int
    approval_status: Literal[
        "not_requested",
        "pending",
        "approved",
        "rejected",
    ]
    # 审批请求保留在 State 中以支持 checkpoint 恢复展示，
    # 但它永远不进入结果白名单。
    approval_request: ApprovalRequest
