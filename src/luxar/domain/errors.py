"""工作流错误领域模型：用稳定业务语言记录失败阶段、类别和恢复建议。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class WorkflowError(BaseModel):
    stage: Literal[
        "requirement_analysis",
        "project_analysis",
        "planning",
        "implementation",
        "project_creation",
        "build",
        "flash",
        "monitor",
        "repair",
    ]

    category: Literal[
        "model_output",
        "environment",
        "dependency",
        "source",
        "linker",
        "timeout",
        "unknown",
        "authentication",
        "rate_limit",
        "service",
        "workspace",
        "serial",
        "approval_rejected",
        "knowledge_insufficient",
        "knowledge_answer_unverified",
    ]

    message: str
    retryable: bool
    user_suggestion: str = ""
    evidence_reference: str | None = None
