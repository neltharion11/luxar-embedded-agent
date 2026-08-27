"""Single dispatch boundary for firmware and dedicated task workflows."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from luxar.application.runtime_mode import FirmwareRuntimeSelection


TaskExecutionMode = Literal["firmware", "inspection", "knowledge"]
WorkflowFamily = Literal[
    "supervisor_firmware",
    "legacy_firmware_rollback",
    "project_inspection",
    "knowledge_task",
]


class RuntimeDispatch(BaseModel):
    """Execution route without conflating dedicated workflows with rollback."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_mode: TaskExecutionMode
    workflow_family: WorkflowFamily
    uses_supervisor: bool
    firmware_runtime: Literal["legacy", "supervisor"]
    firmware_runtime_reason: str


def dispatch_runtime(
    task_mode: TaskExecutionMode,
    selection: FirmwareRuntimeSelection,
) -> RuntimeDispatch:
    if task_mode == "inspection":
        family: WorkflowFamily = "project_inspection"
    elif task_mode == "knowledge":
        family = "knowledge_task"
    elif selection.mode == "supervisor":
        family = "supervisor_firmware"
    else:
        family = "legacy_firmware_rollback"
    return RuntimeDispatch(
        task_mode=task_mode,
        workflow_family=family,
        uses_supervisor=family == "supervisor_firmware",
        firmware_runtime=selection.mode,
        firmware_runtime_reason=selection.reason,
    )


__all__ = [
    "RuntimeDispatch",
    "TaskExecutionMode",
    "WorkflowFamily",
    "dispatch_runtime",
]
