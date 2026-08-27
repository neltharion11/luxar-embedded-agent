"""可恢复的人机交互合同。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.plans import ExecutionPlan


class WorkflowInteraction(BaseModel):
    """工作流暂停时交给用户的受控信息，不包含路径、命令或密钥。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["plan_review", "clarification", "knowledge_write", "repair_review"]
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=2000)
    plan: ExecutionPlan | None = None
    questions: list[str] = Field(default_factory=list, max_length=8)
    options: list[str] = Field(default_factory=list, max_length=12)
    operation: dict[str, object] | None = None
    allow_feedback: bool = True


class WorkflowDecision(BaseModel):
    """用户对一次暂停的回复；feedback 会参与下一轮重新规划。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool = False
    feedback: str = Field(default="", max_length=4000)
    selected_option: str | None = Field(default=None, max_length=300)

