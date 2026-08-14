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


def test_execution_plan_rejects_empty_steps() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan(steps=[])


def test_plan_step_rejects_unsupported_action() -> None:
    with pytest.raises(ValidationError):
        PlanStep(
            kind="flash_project",  # type: ignore[arg-type]
            description="Flash hardware before approval",
        )

