"""把持久化 Agent 状态转换为不会泄漏执行上下文的 Web 视图。"""

from __future__ import annotations

from collections.abc import Sequence

from luxar.database.persistence import (
    AgentInteractionRecord,
    AgentProjectRecord,
    WorkbenchSnapshotRecord,
)
from luxar.domain.agent.acceptance import AcceptanceCriterion
from luxar.domain.agent.capabilities import ProjectCapability
from luxar.domain.agent.changes import ChangeSet
from luxar.domain.agent.failures import AgentFailureRecord
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.tasks import AgentTaskGraph
from luxar.web_contracts import (
    WebAgentAcceptance,
    WebAgentCapability,
    WebAgentChange,
    WebAgentEvidence,
    WebAgentInteraction,
    WebAgentObjective,
    WebAgentRecovery,
    WebAgentSnapshot,
    WebAgentTask,
)


def agent_snapshot_contract(
    *,
    project: str,
    root_index: int,
    record: AgentProjectRecord,
    interactions: Sequence[AgentInteractionRecord],
) -> WebAgentSnapshot:
    objective = ProjectObjective.model_validate(record.objective)
    change_set = ChangeSet.model_validate(record.change_set)
    capabilities = [
        ProjectCapability.model_validate(item) for item in record.capabilities
    ]
    raw_graph = record.snapshot.get("task_graph")
    graph = AgentTaskGraph.model_validate(raw_graph) if raw_graph is not None else None
    raw_acceptance = (
        graph.acceptance_criteria
        if graph is not None
        else record.snapshot.get("acceptance_criteria", [])
    )
    criteria = [
        item
        if isinstance(item, AcceptanceCriterion)
        else AcceptanceCriterion.model_validate(item)
        for item in raw_acceptance
    ]
    evidence_ids = [
        item
        for item in record.snapshot.get("evidence_ids", [])
        if isinstance(item, str)
    ]
    failure_history = [
        AgentFailureRecord.model_validate(item)
        for item in record.snapshot.get("failure_history", [])
    ]
    accepted_by: dict[str, list[str]] = {item: [] for item in evidence_ids}
    for criterion in criteria:
        for evidence_id in criterion.evidence_ids:
            if evidence_id in accepted_by:
                accepted_by[evidence_id].append(criterion.criterion_id)
    return WebAgentSnapshot(
        project=project,
        root_index=root_index,
        revision=record.revision,
        status=str(record.snapshot.get("status", objective.status)),
        objective=WebAgentObjective(
            objective_id=objective.objective_id,
            title=objective.title,
            description=objective.description,
            status=objective.status,
            priority=objective.priority,
            acceptance_criteria=list(objective.acceptance_criteria),
            constraints=list(objective.constraints),
            revision=objective.revision,
        ),
        changes=[
            WebAgentChange(
                operation=change.operation,
                capability_id=change.capability_id,
                rationale=change.rationale,
            )
            for change in change_set.changes
        ],
        tasks=(
            [
                WebAgentTask(
                    task_id=task.task_id,
                    parent_id=task.parent_id,
                    kind=task.kind,
                    title=task.title,
                    description=task.description,
                    depends_on=list(task.depends_on),
                    status=task.status,
                    attempts=task.attempts,
                    max_attempts=task.max_attempts,
                    requires_approval=task.requires_approval,
                    allowed_tools=list(task.allowed_tools),
                    acceptance_criteria=list(task.acceptance_criteria),
                )
                for task in graph.tasks
            ]
            if graph is not None
            else []
        ),
        capabilities=[
            WebAgentCapability(
                capability_id=item.capability_id,
                kind=item.kind,
                parameters=item.parameters,
                status=item.status,
                owners=list(item.owners),
                evidence_ids=list(item.evidence_ids),
                source_kind=item.source_kind,
                confidence=item.confidence,
            )
            for item in capabilities
        ],
        acceptance=[
            WebAgentAcceptance(
                criterion_id=item.criterion_id,
                description=item.description,
                verification_kind=item.verification_kind,
                status=item.status,
                required_evidence=list(item.required_evidence),
                evidence_ids=list(item.evidence_ids),
            )
            for item in criteria
        ],
        evidence=[
            WebAgentEvidence(
                evidence_id=evidence_id,
                kind=evidence_id.partition(":")[0],
                accepted_by=accepted_by[evidence_id],
            )
            for evidence_id in evidence_ids
        ],
        interactions=[
            WebAgentInteraction(
                interaction_id=item.interaction_id,
                objective_id=item.objective_id,
                kind=item.kind,
                payload=item.payload,
            )
            for item in interactions
        ],
        recovery=[
            WebAgentRecovery(
                task_id=item.task_id,
                category=item.category,
                message=item.message,
                attempt=item.attempt,
                repeated=item.repeated,
            )
            for item in failure_history
        ],
        trace=[
            str(item)[:80]
            for item in record.snapshot.get("trace", [])[-80:]
            if isinstance(item, str)
        ],
        current_task_id=(
            str(record.snapshot["current_task_id"])
            if record.snapshot.get("current_task_id") is not None
            else None
        ),
        acceptance_passed=bool(record.snapshot.get("acceptance_passed", False)),
        build_verified=bool(record.snapshot.get("build_verified", False)),
        hardware_function_verified=bool(
            record.snapshot.get("hardware_function_verified", False)
        ),
        blocked_reason=(
            str(record.snapshot["last_error"])
            if record.snapshot.get("last_error")
            else None
        ),
        workflow_family="supervisor_firmware",
        task_mode="firmware",
        thread_id=(
            str(record.snapshot["source_message_id"])
            if record.snapshot.get("source_message_id")
            else None
        ),
        supports_interactions=True,
    )


def workbench_snapshot_contract(
    *,
    project: str,
    root_index: int,
    record: WorkbenchSnapshotRecord,
) -> WebAgentSnapshot:
    """Validate a workflow-neutral persisted workbench snapshot."""

    return WebAgentSnapshot.model_validate(
        {
            **record.snapshot,
            "project": project,
            "root_index": root_index,
            "workflow_family": record.workflow_family,
            "thread_id": record.thread_id,
        }
    )


__all__ = ["agent_snapshot_contract", "workbench_snapshot_contract"]
