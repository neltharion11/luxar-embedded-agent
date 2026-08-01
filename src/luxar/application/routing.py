from __future__ import annotations

from typing import Literal

from luxar.application.state import WorkflowState


def route_after_requirement(
    state: WorkflowState,
) -> Literal["create_plan", "request_clarification"]:
    requirement = state["requirement"]

    if requirement.is_complete:
        return "create_plan"

    return "request_clarification"


def route_after_build(
    state: WorkflowState,
) -> Literal[
    "completed",
    "repair_project",
    "build_project",
    "failed",
]:
    evidence = state["build_evidence"]
    attempts = state.get("attempts", 0)
    max_attempts = state.get("max_attempts", 1)

    if evidence.success:
        return "completed"

    if attempts >= max_attempts:
        return "failed"

    if evidence.error_category in {"source", "linker"}:
        return "repair_project"

    if evidence.error_category == "timeout":
        return "build_project"

    return "failed"
