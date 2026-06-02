from __future__ import annotations

import warnings
from typing import Any

from luxar.models.schemas import EngineeringContext, TaskArtifacts, TaskRunResult


LEGACY_RUN_TASK_WARNING = (
    "run_task/run_task_stream are legacy compatibility entrypoints. "
    "Prefer the LUXAR 0.2.3 runtime surface instead."
)
LEGACY_COMPATIBILITY_MODE = "legacy-run-task"


def stream_event(event_type: str, **payload) -> dict:
    return {"type": event_type, "compatibility_mode": LEGACY_COMPATIBILITY_MODE, **payload}


def warn_legacy_run_task_entrypoint() -> None:
    warnings.warn(LEGACY_RUN_TASK_WARNING, DeprecationWarning, stacklevel=2)


def build_task_result(
    *,
    success: bool,
    mode: str,
    intent: str,
    execution_plan,
    message: str = "",
    engineering: EngineeringContext | None = None,
    project: dict | None = None,
    workflow: dict | None = None,
    driver_pipeline: dict | None = None,
    build_result: dict | None = None,
    report: dict | None = None,
    fixed_files: list[str] | None = None,
    driver_request: dict | None = None,
) -> dict:
    legacy_intent = ""
    if isinstance(execution_plan, dict):
        legacy_intent = execution_plan.get("intent", {}).get("legacy_intent_type", "")
    elif hasattr(execution_plan, "intent"):
        legacy_intent = getattr(execution_plan.intent, "legacy_intent_type", "")
    payload = TaskRunResult(
        success=success,
        mode=mode,
        intent=intent,
        execution_plan=execution_plan.model_dump(mode="json") if hasattr(execution_plan, "model_dump") else dict(execution_plan),
        message=message,
        engineering_context=engineering.model_dump(mode="json") if engineering else {},
        artifacts=TaskArtifacts(
            project=project or {},
            workflow=workflow or {},
            driver_pipeline=driver_pipeline or {},
            build_result=build_result or {},
            report=report or {},
            fixed_files=fixed_files or [],
            driver_request=driver_request or {},
        ),
    ).model_dump(mode="json")
    artifacts = payload.get("artifacts", {})
    payload["project"] = artifacts.get("project", {})
    payload["workflow"] = artifacts.get("workflow", {})
    payload["driver_pipeline"] = artifacts.get("driver_pipeline", {})
    payload["build_result"] = artifacts.get("build_result", {})
    payload["report"] = artifacts.get("report", {})
    payload["fixed_files"] = artifacts.get("fixed_files", [])
    payload["driver_request"] = artifacts.get("driver_request", {})
    payload["compatibility_mode"] = LEGACY_COMPATIBILITY_MODE
    payload["legacy_warning"] = LEGACY_RUN_TASK_WARNING
    payload["legacy_intent"] = legacy_intent
    return payload


def consume_stream_result(stream) -> dict:
    while True:
        try:
            next(stream)
        except StopIteration as stop:
            return stop.value


def workflow_name_from_execution_plan(execution_plan) -> str:
    intent = execution_plan.intent
    return intent.legacy_workflow or intent.recommended_workflow or intent.intent_type


def prepare_legacy_task_invocation(
    *,
    task: str,
    project_name: str,
    docs: list[str] | None,
    dry_run: bool,
    plan_only: bool,
    driver_library_root: str,
    prepare_task_execution,
    workflow_name_from_execution_plan_fn=workflow_name_from_execution_plan,
) -> tuple[list[str], Any, EngineeringContext | None, str]:
    warn_legacy_run_task_entrypoint()
    docs = docs or []
    execution_plan, engineering = prepare_task_execution(
        task=task,
        project_name=project_name,
        docs=docs,
        dry_run=dry_run,
        plan_only=plan_only,
        driver_library_root=driver_library_root,
    )
    workflow_name = workflow_name_from_execution_plan_fn(execution_plan)
    return docs, execution_plan, engineering, workflow_name


def build_workflow_started_event(
    *,
    task: str,
    workflow_name: str,
    execution_plan,
    engineering: EngineeringContext | None,
) -> dict:
    return stream_event(
        "workflow_started",
        workflow=workflow_name,
        message=f"开始执行任务：{task.strip()}",
        payload={
            "execution_plan": execution_plan.model_dump(mode="json"),
            "engineering_context": engineering.model_dump(mode="json") if engineering else {},
        },
    )


def stream_legacy_task_result(*, stream, workflow_name: str) -> Any:
    while True:
        try:
            event = next(stream)
        except StopIteration as stop:
            result = stop.value
            break
        if "workflow" not in event:
            event["workflow"] = workflow_name
        yield event
    final_type = "workflow_finished" if result["success"] else "workflow_failed"
    yield stream_event(final_type, workflow=workflow_name, message=result["message"], payload={"result": result})
