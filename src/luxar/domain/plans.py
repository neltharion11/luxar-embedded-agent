"""执行计划领域模型：约束 Agent 可以提出的工程步骤及其顺序。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    # 当前动作词表是封闭的；LLM 不能凭空发明 Graph 不支持的动作。
    kind: Literal["create_project", "build_project"]
    description: str


class ExecutionPlan(BaseModel):
    # min_length=1 保证计划至少包含一个实际动作。
    steps: list[PlanStep] = Field(min_length=1)
