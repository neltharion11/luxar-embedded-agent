from __future__ import annotations

from langgraph.runtime import Runtime

from luxar.application.context import RuntimeContext
from luxar.application.state import WorkflowState


def analyze_requirement(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    requirement = runtime.context.requirement_parser.parse(
        state["task_text"]
    )

    return {
        "requirement": requirement,
        "status": "requirement_analyzed",
        "trace": [
            *state.get("trace", []),
            "analyze_requirement",
        ],
    }


def create_plan(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    requirement = state["requirement"]
    planner = runtime.context.planner
    plan = planner.create_plan(requirement)

    return {
        "plan": plan,
        "status": "planned",
        "trace": [
            *state.get("trace", []),
            "create_plan",
        ],
    }


def build_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    espidf = runtime.context.espidf
    project_path = runtime.context.project_path
    evidence = espidf.build(project_path)

    next_attempt = state.get("attempts", 0) + 1

    return {
        "build_evidence": evidence,
        "attempts": next_attempt,
        "status": "building",
        "trace": [
            *state.get("trace", []),
            "build_project",
        ],
    }


def request_clarification(
    state: WorkflowState,
) -> dict[str, object]:
    return {
        "status": "needs_clarification",
        "trace": [
            *state.get("trace", []),
            "request_clarification",
        ],
    }


def completed(
    state: WorkflowState,
) -> dict[str, object]:
    return {
        "status": "completed",
        "trace": [
            *state.get("trace", []),
            "completed",
        ],
    }


def failed(
    state: WorkflowState,
) -> dict[str, object]:
    return {
        "status": "failed",
        "trace": [
            *state.get("trace", []),
            "failed",
        ],
    }


def repair_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    project_path = runtime.context.project_path
    workspace = runtime.context.workspace
    repair_planner = runtime.context.repair_planner

    files = workspace.read_project_files(project_path)

    repair = repair_planner.create_repair(
        state["requirement"],
        state["plan"],
        state["build_evidence"],
        files,
    )

    changed_files = workspace.apply_repair(
        project_path,
        repair,
    )

    return {
        "repair_plan": repair,
        "changed_files": changed_files,
        "status": "repaired",
        "trace": [
            *state.get("trace", []),
            "repair_project",
        ],
    }