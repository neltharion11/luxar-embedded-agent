"""工作流结果展示合同：为 CLI 和 Web 提供同一份安全白名单结果。"""

from __future__ import annotations

from pydantic import BaseModel

from luxar.application.state import WorkflowState


def exit_code_for_state(state: WorkflowState) -> int:
    """把三个业务终态映射成稳定的进程/接口结果码。"""

    return {
        "completed": 0,
        "needs_clarification": 3,
        "failed": 4,
    }.get(state.get("status"), 4)


def _serialize_model(value: BaseModel | None) -> dict[str, object] | None:
    if value is None:
        return None
    return value.model_dump(mode="json")


def state_to_result(state: WorkflowState) -> dict[str, object]:
    """只选择允许离开应用边界的字段，不序列化整个 State。

    approval_request、task_text 与原始日志永远不进入本白名单。
    """

    return {
        "status": state.get("status", "failed"),
        "exit_code": exit_code_for_state(state),
        "attempts": state.get("attempts", 0),
        "requirement": _serialize_model(state.get("requirement")),
        "plan": _serialize_model(state.get("plan")),
        "created_project": _serialize_model(
            state.get("created_project")
        ),
        "build_evidence": _serialize_model(state.get("build_evidence")),
        "flash_evidence": _serialize_model(state.get("flash_evidence")),
        "monitor_evidence": _serialize_model(
            state.get("monitor_evidence")
        ),
        "device_diagnosis": _serialize_model(
            state.get("device_diagnosis")
        ),
        "approval_status": state.get(
            "approval_status",
            "not_requested",
        ),
        "repair_plan": _serialize_model(state.get("repair_plan")),
        "changed_files": list(state.get("changed_files", [])),
        "error": _serialize_model(state.get("error")),
        "trace": list(state.get("trace", [])),
    }
