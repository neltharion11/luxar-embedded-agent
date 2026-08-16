import pytest

from luxar.application.routing import (
    route_after_approval,
    route_after_build,
    route_after_diagnosis,
    route_after_dispatch,
    route_after_flash,
    route_after_project_creation,
    route_after_requirement,
)
from luxar.domain.devices import DeviceDiagnosis, FlashEvidence
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


def test_dispatch_routes_flash_step_to_approval() -> None:
    state = {"pending_step_kind": "flash_project"}

    assert route_after_dispatch(state) == "request_flash_approval"


def test_dispatch_routes_monitor_step_to_monitor_node() -> None:
    state = {"pending_step_kind": "monitor_project"}

    assert route_after_dispatch(state) == "monitor_project"


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


def test_approved_flash_routes_to_flash_node() -> None:
    state = {"approval_status": "approved"}

    assert route_after_approval(state) == "flash_project"


@pytest.mark.parametrize(
    "approval_status",
    ["rejected", "not_requested", "pending"],
)
def test_not_approved_flash_routes_to_failed(
    approval_status: str,
) -> None:
    state = {"approval_status": approval_status}

    assert route_after_approval(state) == "failed"


def test_successful_flash_continues_plan() -> None:
    state = {
        "flash_evidence": FlashEvidence(
            success=True,
            command=["idf.py", "-p", "COM3", "flash"],
            return_code=0,
            port="COM3",
        )
    }

    assert route_after_flash(state) == "execute_next_step"


@pytest.mark.parametrize(
    ("error_category", "flash_attempts", "expected"),
    [
        ("serial", 1, "flash_project"),
        ("timeout", 1, "flash_project"),
        ("serial", 2, "failed"),
        ("timeout", 2, "failed"),
        ("environment", 1, "failed"),
        ("unknown", 1, "failed"),
    ],
)
def test_failed_flash_routes_by_category_and_budget(
    error_category: str | None,
    flash_attempts: int,
    expected: str,
) -> None:
    state = {
        "flash_evidence": FlashEvidence(
            success=False,
            command=["idf.py", "-p", "COM3", "flash"],
            return_code=1,
            port="COM3",
            error_category=error_category,  # type: ignore[arg-type]
        ),
        "flash_attempts": flash_attempts,
    }

    assert route_after_flash(state) == expected


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


def test_healthy_diagnosis_routes_to_completed() -> None:
    state = {
        "device_diagnosis": DeviceDiagnosis(
            healthy=True,
            repair_needed=False,
            summary="运行正常",
        )
    }

    assert route_after_diagnosis(state) == "completed"


def test_repair_needed_diagnosis_routes_to_repair() -> None:
    state = {
        "device_diagnosis": DeviceDiagnosis(
            healthy=False,
            repair_needed=True,
            summary="看门狗超时",
        )
    }

    assert route_after_diagnosis(state) == "repair_project"


def test_unhealthy_diagnosis_without_repair_routes_to_failed() -> None:
    state = {
        "device_diagnosis": DeviceDiagnosis(
            healthy=False,
            repair_needed=False,
            summary="疑似硬件故障",
        )
    }

    assert route_after_diagnosis(state) == "failed"


def test_build_success_with_monitor_origin_routes_to_flash_approval() -> None:
    state = {
        "build_evidence": BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
        ),
        "repair_origin": "monitor",
    }

    assert route_after_build(state) == "request_flash_approval"


def test_flash_success_with_monitor_origin_routes_to_monitor() -> None:
    state = {
        "flash_evidence": FlashEvidence(
            success=True,
            command=["idf.py", "-p", "COM3", "flash"],
            return_code=0,
            port="COM3",
        ),
        "repair_origin": "monitor",
    }

    assert route_after_flash(state) == "monitor_project"
