import pytest

from luxar.application.routing import (
    route_after_build,
    route_after_dispatch,
    route_after_project_creation,
    route_after_requirement,
)
from luxar.domain.evidence import BuildEvidence
from luxar.domain.projects import ProjectEvidence
from luxar.domain.requirements import FirmwareRequirement


def test_complete_requirement_routes_to_planning() -> None:
    state = {
        "requirement": FirmwareRequirement(
            target="esp32",
            feature="gpio_blink",
            gpio=2,
        )
    }

    destination = route_after_requirement(state)

    assert destination == "create_plan"


def test_incomplete_requirement_routes_to_clarification() -> None:
    state = {
        "requirement": FirmwareRequirement(
            target="esp32",
            feature="gpio_blink",
            missing_fields=["gpio"],
        )
    }

    destination = route_after_requirement(state)

    assert destination == "request_clarification"


def test_dispatch_routes_build_step_to_build_node() -> None:
    state = {"pending_step_kind": "build_project"}

    assert route_after_dispatch(state) == "build_project"


def test_dispatch_routes_creation_step_to_creation_node() -> None:
    state = {"pending_step_kind": "create_project"}

    assert route_after_dispatch(state) == "create_project"


def test_dispatch_routes_none_to_completed() -> None:
    state = {"pending_step_kind": None}

    assert route_after_dispatch(state) == "completed"


@pytest.mark.parametrize(
    "pending_kind",
    ["flash_project", "monitor_project"],
)
def test_dispatch_routes_not_yet_supported_steps_to_failed(
    pending_kind: str,
) -> None:
    state = {"pending_step_kind": pending_kind}

    assert route_after_dispatch(state) == "failed"


def test_successful_creation_continues_plan() -> None:
    state = {
        "created_project": ProjectEvidence(
            success=True,
            command=["idf.py", "create-project", "blink"],
            return_code=0,
            created_dir="blink",
        )
    }

    assert route_after_project_creation(state) == "execute_next_step"


def test_failed_creation_terminates() -> None:
    state = {
        "created_project": ProjectEvidence(
            success=False,
            command=["idf.py", "create-project", "blink"],
            return_code=1,
            error_category="environment",
        )
    }

    assert route_after_project_creation(state) == "failed"


def test_successful_final_attempt_continues_plan() -> None:
    state = {
        "build_evidence": BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
        ),
        "attempts": 3,
        "max_attempts": 3,
    }

    destination = route_after_build(state)

    assert destination == "execute_next_step"


@pytest.mark.parametrize(
    ("error_category", "attempts", "max_attempts", "expected"),
    [
        ("source", 1, 3, "repair_project"),
        ("linker", 2, 3, "repair_project"),
        ("timeout", 1, 2, "build_project"),
        ("dependency", 1, 3, "failed"),
        ("environment", 1, 3, "failed"),
        ("unknown", 1, 3, "failed"),
        (None, 1, 3, "failed"),
        ("source", 1, 1, "failed"),
        ("linker", 2, 2, "failed"),
        ("timeout", 3, 3, "failed"),
    ],
)
def test_failed_build_routes_by_category_and_attempt_budget(
    error_category: str | None,
    attempts: int,
    max_attempts: int,
    expected: str,
) -> None:
    state = {
        "build_evidence": BuildEvidence(
            success=False,
            command=["idf.py", "build"],
            return_code=1,
            error_category=error_category,  # type: ignore[arg-type]
        ),
        "attempts": attempts,
        "max_attempts": max_attempts,
    }

    destination = route_after_build(state)

    assert destination == expected
