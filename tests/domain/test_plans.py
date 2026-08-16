import pytest
from pydantic import ValidationError

from luxar.domain.plans import ExecutionPlan, PlanStep


def test_execution_plan_preserves_ordered_supported_steps() -> None:
    plan = ExecutionPlan(
        steps=[
            PlanStep(
                kind="create_project",
                description="Create a minimal ESP-IDF project",
            ),
            PlanStep(
                kind="build_project",
                description="Build the ESP-IDF project",
            ),
        ]
    )

    assert [step.kind for step in plan.steps] == [
        "create_project",
        "build_project",
    ]


def test_execution_plan_accepts_full_pipeline_order() -> None:
    plan = ExecutionPlan(
        steps=[
            PlanStep(
                kind="create_project",
                description="Create the project",
            ),
            PlanStep(
                kind="build_project",
                description="Build the firmware",
            ),
            PlanStep(
                kind="flash_project",
                description="Flash the board",
            ),
            PlanStep(
                kind="monitor_project",
                description="Monitor serial output",
            ),
        ]
    )

    assert [step.kind for step in plan.steps] == [
        "create_project",
        "build_project",
        "flash_project",
        "monitor_project",
    ]


def test_execution_plan_rejects_empty_steps() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan(steps=[])


def test_plan_step_rejects_unsupported_action() -> None:
    with pytest.raises(ValidationError):
        PlanStep(
            kind="erase_flash",  # type: ignore[arg-type]
            description="A vocabulary action the graph does not support",
        )


def test_plan_rejects_multiple_project_creation_steps() -> None:
    with pytest.raises(ValidationError, match="more than once"):
        ExecutionPlan(
            steps=[
                PlanStep(kind="create_project", description="first"),
                PlanStep(kind="build_project", description="build"),
                PlanStep(kind="create_project", description="second"),
            ]
        )


def test_plan_rejects_project_creation_after_other_steps() -> None:
    with pytest.raises(ValidationError, match="first step"):
        ExecutionPlan(
            steps=[
                PlanStep(kind="build_project", description="build first"),
                PlanStep(kind="create_project", description="create later"),
            ]
        )


def test_plan_rejects_flash_without_earlier_build() -> None:
    with pytest.raises(ValidationError, match="earlier build_project"):
        ExecutionPlan(
            steps=[
                PlanStep(kind="flash_project", description="flash only"),
            ]
        )


def test_plan_rejects_monitor_without_earlier_flash() -> None:
    with pytest.raises(ValidationError, match="earlier flash_project"):
        ExecutionPlan(
            steps=[
                PlanStep(kind="build_project", description="build"),
                PlanStep(kind="monitor_project", description="monitor"),
            ]
        )
