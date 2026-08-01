from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    kind: Literal["create_project", "build_project"]
    description: str


class ExecutionPlan(BaseModel):
    steps: list[PlanStep] = Field(min_length=1)