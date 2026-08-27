"""Supervisor 高风险任务的可持久化审批合同。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["task_approval"] = "task_approval"
    task_id: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1200)
    operation: str = Field(min_length=1, max_length=120)
    risks: list[str] = Field(min_length=1, max_length=12)
    task_description: str = Field(default="", max_length=4000)
    planned_actions: list[str] = Field(default_factory=list, max_length=40)
    tools: list[str] = Field(default_factory=list, max_length=40)
    affected_targets: list[str] = Field(default_factory=list, max_length=100)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=100)
    preserve_conditions: list[str] = Field(default_factory=list, max_length=100)
    allow_feedback: bool = True


__all__ = ["AgentApprovalRequest"]
