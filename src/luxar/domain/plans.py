"""执行计划领域模型：约束 Agent 可以提出的工程步骤及其顺序。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PlanStep(BaseModel):
    # 当前动作词表是封闭的；LLM 不能凭空发明 Graph 不支持的动作。
    kind: Literal[
        "create_project",
        "implement_change",
        "build_project",
        "flash_project",
        "monitor_project",
    ]
    description: str


class ExecutionPlan(BaseModel):
    # min_length=1 保证计划至少包含一个实际动作。
    steps: list[PlanStep] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_step_ordering(self) -> ExecutionPlan:
        # 每个字段完成类型验证后，再检查步骤之间的先后依赖。
        kinds = [step.kind for step in self.steps]

        create_positions = [
            index
            for index, kind in enumerate(kinds)
            if kind == "create_project"
        ]

        # 项目只能创建一次，而且必须是整个计划的第一个动作。
        if len(create_positions) > 1:
            raise ValueError(
                "plan cannot create the project more than once"
            )

        if create_positions and create_positions[0] != 0:
            raise ValueError(
                "create_project must be the first step"
            )

        implement_positions = [
            index
            for index, kind in enumerate(kinds)
            if kind == "implement_change"
        ]
        if len(implement_positions) > 1:
            raise ValueError("plan cannot implement the change more than once")
        if (
            implement_positions
            and create_positions
            and implement_positions[0] < create_positions[0]
        ):
            raise ValueError("implement_change must follow create_project")
        if (
            implement_positions
            and "build_project" in kinds
            and implement_positions[0] > kinds.index("build_project")
        ):
            raise ValueError("implement_change must precede build_project")

        # 烧录前必须已经构建过固件，否则没有可烧录的产物。
        if "flash_project" in kinds:
            flash_index = kinds.index("flash_project")
            if "build_project" not in kinds[:flash_index]:
                raise ValueError(
                    "flash_project requires an earlier build_project step"
                )

        # 监控前必须已经烧录过固件，否则设备上没有新程序可观察。
        if "monitor_project" in kinds:
            monitor_index = kinds.index("monitor_project")
            if "flash_project" not in kinds[:monitor_index]:
                raise ValueError(
                    "monitor_project requires an earlier flash_project step"
                )

        return self
