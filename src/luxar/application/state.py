from __future__ import annotations

from typing import Literal, TypedDict

from luxar.domain.errors import WorkflowError
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan
from luxar.domain.requirements import FirmwareRequirement


WorkflowStatus = Literal[
    "requirement_analyzed",
    "needs_clarification",
    "planned",
    "building",
    "retrying",
    "completed",
    "failed",
]


class WorkflowState(TypedDict, total=False):
    task_text: str
    requirement: FirmwareRequirement
    plan: ExecutionPlan
    build_evidence: BuildEvidence
    error: WorkflowError
    attempts: int
    max_attempts: int
    status: WorkflowStatus
    trace: list[str]