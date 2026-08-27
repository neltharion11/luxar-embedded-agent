"""把 Supervisor State 映射到应用持久化 Port。

该模块不让数据库知道 Pydantic/ LangGraph 细节；它只负责快照的序列化和
恢复，便于后续在 graph checkpoint 恢复后重新装载项目目标与能力基线。
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from luxar.application.agent_state import AgentState
from luxar.database.persistence import AgentInteractionRecord, PersistencePort
from luxar.domain.agent.capabilities import ProjectCapability
from luxar.domain.agent.acceptance import AcceptanceCriterion
from luxar.domain.agent.approvals import AgentApprovalRequest
from luxar.domain.agent.build_recovery import BuildRecoveryDecision
from luxar.domain.agent.changes import ChangeSet
from luxar.domain.agent.code_changes import ChangeBundleValidation
from luxar.domain.agent.failures import AgentFailureRecord
from luxar.domain.agent.hardware import HardwareValidationReport
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.tasks import AgentTaskGraph
from luxar.domain.agent.verification import (
    FirmwareResourceEvidence,
    VerificationPlan,
    VerificationRun,
)
from luxar.domain.devices import FlashEvidence, MonitorEvidence
from luxar.domain.evidence import BuildEvidence


_SNAPSHOT_FIELDS = (
    "source_message_id",
    "status",
    "trace",
    "task_graph",
    "acceptance_criteria",
    "evidence_ids",
    "approval_request",
    "approval_status",
    "verification_plan",
    "verification_runs",
    "hardware_report",
    "hardware_validated",
    "hardware_blocked",
    "build_evidence",
    "flash_evidence",
    "monitor_evidence",
    "firmware_resource_evidence",
    "build_recovery",
    "build_verified",
    "hardware_function_verified",
    "failure_history",
    "task_feedback",
    "current_task_id",
    "acceptance_passed",
    "last_error",
)


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Agent snapshot contains unsupported value: {type(value).__name__}")


def agent_state_snapshot(state: AgentState) -> dict[str, object]:
    """只提取可恢复领域状态；RuntimeContext 和源码正文不会进入数据库。"""

    return {
        field: _jsonable(state[field])
        for field in _SNAPSHOT_FIELDS
        if field in state
    }


def _restore_snapshot(snapshot: Mapping[str, object]) -> dict[str, object]:
    restored = dict(snapshot)
    model_fields: tuple[tuple[str, type[BaseModel]], ...] = (
        ("task_graph", AgentTaskGraph),
        ("verification_plan", VerificationPlan),
        ("hardware_report", HardwareValidationReport),
        ("build_evidence", BuildEvidence),
        ("flash_evidence", FlashEvidence),
        ("monitor_evidence", MonitorEvidence),
        ("firmware_resource_evidence", FirmwareResourceEvidence),
        ("build_recovery", BuildRecoveryDecision),
        ("approval_request", AgentApprovalRequest),
    )
    for field, model in model_fields:
        if field in restored:
            restored[field] = model.model_validate(restored[field])
    if "acceptance_criteria" in restored:
        restored["acceptance_criteria"] = [
            AcceptanceCriterion.model_validate(item)
            for item in restored["acceptance_criteria"]  # type: ignore[union-attr]
        ]
    if "verification_runs" in restored:
        restored["verification_runs"] = {
            str(task_id): VerificationRun.model_validate(run)
            for task_id, run in restored["verification_runs"].items()  # type: ignore[union-attr]
        }
    if "failure_history" in restored:
        restored["failure_history"] = [
            AgentFailureRecord.model_validate(item)
            for item in restored["failure_history"]  # type: ignore[union-attr]
        ]
    return restored


def save_agent_snapshot(
    persistence: PersistencePort,
    project_key: str,
    state: AgentState,
    *,
    interaction: Mapping[str, object] | None = None,
) -> None:
    objective = state.get("objective")
    change_set = state.get("change_set")
    if objective is None or change_set is None:
        return
    if isinstance(objective, dict):
        objective = ProjectObjective.model_validate(objective)
    if isinstance(change_set, dict):
        change_set = ChangeSet.model_validate(change_set)
    capabilities = [
        item if isinstance(item, ProjectCapability) else ProjectCapability.model_validate(item)
        for item in state.get("capabilities", [])
    ]
    persistence.save_agent_project(
        project_key=project_key,
        objective=objective.model_dump(mode="json"),
        change_set=change_set.model_dump(mode="json"),
        revision=objective.revision,
        capabilities=[item.model_dump(mode="json") for item in capabilities],
        snapshot=agent_state_snapshot(state),
    )
    thread_id = str(state.get("source_message_id", "")).strip()
    if thread_id:
        persistence.save_workbench_snapshot(
            project_key=project_key,
            workflow_family="supervisor_firmware",
            thread_id=thread_id,
            snapshot={},
        )
    if interaction is not None:
        interaction_id = str(interaction.get("interaction_id", ""))
        kind = str(interaction.get("kind", ""))
        payload = interaction.get("payload", {})
        if interaction_id and kind and isinstance(payload, dict):
            persistence.append_agent_interaction(
                interaction_id=interaction_id,
                project_key=project_key,
                objective_id=objective.objective_id,
                kind=kind,
                payload=payload,
            )

    for task_id, raw_validation in state.get("change_validations", {}).items():
        validation = (
            raw_validation
            if isinstance(raw_validation, ChangeBundleValidation)
            else ChangeBundleValidation.model_validate(raw_validation)
        )
        persistence.append_agent_interaction(
            interaction_id=(
                f"change:{project_key}:{task_id}:{validation.after_fingerprint}"
            ),
            project_key=project_key,
            objective_id=objective.objective_id,
            kind="change_applied",
            payload={
                "task_id": task_id,
                "before_fingerprint": validation.before_fingerprint,
                "after_fingerprint": validation.after_fingerprint,
                "changed_files": list(validation.changed_files),
                "diff_summary": list(validation.diff_summary),
            },
        )


def load_agent_snapshot(
    persistence: PersistencePort,
    project_key: str,
) -> AgentState:
    record = persistence.get_agent_project(project_key)
    if record is None:
        return {}
    state: AgentState = {
        "objective": ProjectObjective.model_validate(record.objective),
        "change_set": ChangeSet.model_validate(record.change_set),
        "capabilities": [
            ProjectCapability.model_validate(item) for item in record.capabilities
        ],
    }
    state.update(_restore_snapshot(record.snapshot))  # type: ignore[typeddict-item]
    return state


def load_agent_interactions(
    persistence: PersistencePort,
    project_key: str,
    *,
    limit: int = 100,
) -> list[AgentInteractionRecord]:
    return persistence.get_agent_interactions(project_key, limit=limit)
