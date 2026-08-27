"""Read-only usage and recovery audit for the legacy retirement window."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from luxar.database.persistence import (
    PendingRuntimeApproval,
    PersistencePort,
    RuntimeObservationRecord,
)


MINIMUM_RUNTIME_OBSERVATION_DAYS = 30
RuntimeRecordKind = Literal[
    "supervisor_firmware",
    "legacy_firmware_rollback",
    "specialized",
    "unclassified",
]


class RuntimeRetirementAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    observed_at: datetime
    baseline_started_at: datetime
    observed_days: float = Field(ge=0)
    minimum_observation_days: int = Field(ge=1)
    durable: bool
    total_runs: int = Field(ge=0)
    supervisor_firmware_runs: int = Field(ge=0)
    legacy_firmware_rollback_runs: int = Field(ge=0)
    specialized_runs: int = Field(ge=0)
    unclassified_runs: int = Field(ge=0)
    active_legacy_or_unclassified_runs: int = Field(ge=0)
    pending_legacy_or_unclassified_approvals: int = Field(ge=0)
    checkpoint_inventory_complete: bool
    orphan_checkpoint_threads: int = Field(ge=0)
    rollback_usage_gate_candidate: bool
    recovery_dependency_gate_candidate: bool
    qualifies_as_release_evidence: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    evidence_id: str


def _kind(
    workflow_family: str | None,
    agent_runtime: str | None,
) -> RuntimeRecordKind:
    if workflow_family == "supervisor_firmware":
        return "supervisor_firmware"
    if workflow_family == "legacy_firmware_rollback":
        return "legacy_firmware_rollback"
    if workflow_family in {"project_inspection", "knowledge_task"}:
        return "specialized"
    # Pre-dispatch Supervisor records are unambiguous. Old ``legacy`` records
    # may actually be inspection/knowledge tasks and must not be guessed.
    if workflow_family is None and agent_runtime == "supervisor":
        return "supervisor_firmware"
    return "unclassified"


def _approval_kind(record: PendingRuntimeApproval) -> RuntimeRecordKind:
    return _kind(record.workflow_family, record.agent_runtime)


def inspect_sqlite_checkpoint_threads(
    checkpoint_path: Path,
) -> set[str]:
    """Read thread identifiers only; checkpoint payloads are never loaded."""

    path = checkpoint_path.expanduser().resolve()
    if not path.exists():
        return set()
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'checkpoints'"
        ).fetchone()
        if table is None:
            return set()
        rows = connection.execute(
            "SELECT DISTINCT thread_id FROM checkpoints"
        ).fetchall()
        return {str(row[0]) for row in rows if row[0]}
    finally:
        connection.close()


def audit_runtime_retirement(
    persistence: PersistencePort,
    *,
    observed_at: datetime | None = None,
    minimum_observation_days: int = MINIMUM_RUNTIME_OBSERVATION_DAYS,
    checkpoint_thread_ids: set[str] | None = None,
) -> RuntimeRetirementAudit:
    if minimum_observation_days <= 0:
        raise ValueError("minimum_observation_days 必须为正整数")
    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    baseline = persistence.get_runtime_observation_baseline()
    if baseline.tzinfo is None:
        baseline = baseline.replace(tzinfo=timezone.utc)
    baseline = baseline.astimezone(timezone.utc)
    observed_seconds = max(0.0, (now - baseline).total_seconds())
    observed_days = observed_seconds / 86_400

    window_start = max(
        baseline,
        now - timedelta(days=minimum_observation_days),
    )
    runs = persistence.list_runtime_observations(since=window_start)
    all_runs = persistence.list_runtime_observations(
        since=datetime.min.replace(tzinfo=timezone.utc)
    )
    approvals = persistence.list_pending_runtime_approvals()

    counts: dict[RuntimeRecordKind, int] = {
        "supervisor_firmware": 0,
        "legacy_firmware_rollback": 0,
        "specialized": 0,
        "unclassified": 0,
    }
    for record in runs:
        kind = _kind(record.workflow_family, record.agent_runtime)
        counts[kind] += 1
    active_recovery = sum(
        1
        for record in all_runs
        if (
            record.status in {"running", "pending_approval"}
            and _kind(record.workflow_family, record.agent_runtime)
            in {"legacy_firmware_rollback", "unclassified"}
        )
    )
    pending_recovery = sum(
        _approval_kind(record)
        in {"legacy_firmware_rollback", "unclassified"}
        for record in approvals
    )

    checkpoint_complete = checkpoint_thread_ids is not None
    known_thread_ids = {record.thread_id for record in all_runs}
    orphan_checkpoints = (
        len(checkpoint_thread_ids - known_thread_ids)
        if checkpoint_thread_ids is not None
        else 0
    )
    enough_time = observed_days >= minimum_observation_days
    observation_complete = counts["unclassified"] == 0
    rollback_clear = (
        persistence.durable
        and enough_time
        and observation_complete
        and counts["legacy_firmware_rollback"] == 0
    )
    recovery_clear = (
        persistence.durable
        and checkpoint_complete
        and active_recovery == 0
        and pending_recovery == 0
        and orphan_checkpoints == 0
    )
    blocking: list[str] = []
    if not persistence.durable:
        blocking.append("storage_not_durable")
    if not enough_time:
        blocking.append("observation_window_too_short")
    if counts["legacy_firmware_rollback"]:
        blocking.append("legacy_firmware_rollback_observed")
    if counts["unclassified"]:
        blocking.append("unclassified_historical_runs")
    if active_recovery:
        blocking.append("active_legacy_or_unclassified_runs")
    if pending_recovery:
        blocking.append("pending_legacy_or_unclassified_approvals")
    if not checkpoint_complete:
        blocking.append("checkpoint_inventory_unavailable")
    if orphan_checkpoints:
        blocking.append("orphan_checkpoint_threads")

    evidence_payload = {
        "baseline": baseline.isoformat(),
        "observed_at": now.isoformat(),
        "minimum_days": minimum_observation_days,
        "durable": persistence.durable,
        "counts": counts,
        "active_recovery": active_recovery,
        "pending_recovery": pending_recovery,
        "checkpoint_complete": checkpoint_complete,
        "orphan_checkpoints": orphan_checkpoints,
    }
    digest = hashlib.sha256(
        json.dumps(
            evidence_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    qualifies = rollback_clear and recovery_clear
    return RuntimeRetirementAudit(
        observed_at=now,
        baseline_started_at=baseline,
        observed_days=observed_days,
        minimum_observation_days=minimum_observation_days,
        durable=persistence.durable,
        total_runs=len(runs),
        supervisor_firmware_runs=counts["supervisor_firmware"],
        legacy_firmware_rollback_runs=counts["legacy_firmware_rollback"],
        specialized_runs=counts["specialized"],
        unclassified_runs=counts["unclassified"],
        active_legacy_or_unclassified_runs=active_recovery,
        pending_legacy_or_unclassified_approvals=pending_recovery,
        checkpoint_inventory_complete=checkpoint_complete,
        orphan_checkpoint_threads=orphan_checkpoints,
        rollback_usage_gate_candidate=rollback_clear,
        recovery_dependency_gate_candidate=recovery_clear,
        qualifies_as_release_evidence=qualifies,
        blocking_reasons=blocking,
        evidence_id=f"runtime-retirement-audit:{digest}",
    )


__all__ = [
    "MINIMUM_RUNTIME_OBSERVATION_DAYS",
    "RuntimeRetirementAudit",
    "audit_runtime_retirement",
    "inspect_sqlite_checkpoint_threads",
]
