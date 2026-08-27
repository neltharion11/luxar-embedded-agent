"""项目长期目标模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectObjective(BaseModel):
    """跨多轮消息持续存在的项目目标，而不是某一条用户消息。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    objective_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=8000)
    status: Literal["draft", "active", "blocked", "completed", "cancelled"] = "active"
    priority: int = Field(default=50, ge=1, le=100)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=40)
    constraints: list[str] = Field(default_factory=list, max_length=80)
    source_message_ids: list[str] = Field(default_factory=list, max_length=100)
    revision: int = Field(default=1, ge=1)

