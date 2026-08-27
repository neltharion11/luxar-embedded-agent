from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

from luxar.application.runtime_observation import (
    audit_runtime_retirement,
    inspect_sqlite_checkpoint_threads,
)
from luxar.database import PendingApprovalRecord, SQLitePersistence
from luxar.database.persistence import TransientPersistence


class DurableTransientPersistence(TransientPersistence):
    durable = True


def _start(
    repository: TransientPersistence,
    thread_id: str,
    runtime_config: dict[str, object],
) -> None:
    repository.start_run(
        thread_id=thread_id,
        task_key=f"0:{thread_id}",
        project_name=thread_id,
        root_index=0,
        task_text="audit",
        runtime_config=runtime_config,
    )
    repository.finish_run(thread_id, status="completed", result={})


def test_audit_classifies_runtime_families_without_guessing_old_legacy() -> None:
    repository = DurableTransientPersistence()
    _start(
        repository,
        "supervisor",
        {"workflow_family": "supervisor_firmware"},
    )
    _start(
        repository,
        "rollback",
        {"workflow_family": "legacy_firmware_rollback"},
    )
    _start(
        repository,
        "inspection",
        {"workflow_family": "project_inspection"},
    )
    _start(repository, "old-supervisor", {"agent_runtime": "supervisor"})
    _start(repository, "old-legacy", {"agent_runtime": "legacy"})

    report = audit_runtime_retirement(
        repository,
        observed_at=repository.get_runtime_observation_baseline()
        + timedelta(minutes=1),
        checkpoint_thread_ids=set(),
    )

    assert report.total_runs == 5
    assert report.supervisor_firmware_runs == 2
    assert report.legacy_firmware_rollback_runs == 1
    assert report.specialized_runs == 1
    assert report.unclassified_runs == 1
    assert "legacy_firmware_rollback_observed" in report.blocking_reasons
    assert "unclassified_historical_runs" in report.blocking_reasons


def test_durable_full_window_without_runs_produces_candidate_evidence() -> None:
    repository = DurableTransientPersistence()
    baseline = repository.get_runtime_observation_baseline()

    report = audit_runtime_retirement(
        repository,
        observed_at=baseline + timedelta(days=31),
        checkpoint_thread_ids=set(),
    )

    assert report.rollback_usage_gate_candidate is True
    assert report.recovery_dependency_gate_candidate is True
    assert report.qualifies_as_release_evidence is True
    assert report.blocking_reasons == []
    assert report.evidence_id.startswith("runtime-retirement-audit:")


def test_pending_historical_approval_and_orphan_checkpoint_block_removal() -> None:
    repository = DurableTransientPersistence()
    repository.save_pending_approval(
        PendingApprovalRecord(
            task_key="0:old",
            project_name="old",
            root_index=0,
            thread_id="old-thread",
            request={"kind": "knowledge_write"},
            runtime_config={"agent_runtime": "legacy"},
        )
    )
    baseline = repository.get_runtime_observation_baseline()

    report = audit_runtime_retirement(
        repository,
        observed_at=baseline + timedelta(days=31),
        checkpoint_thread_ids={"orphan-thread"},
    )

    assert report.pending_legacy_or_unclassified_approvals == 1
    assert report.orphan_checkpoint_threads == 1
    assert report.recovery_dependency_gate_candidate is False
    assert "pending_legacy_or_unclassified_approvals" in (
        report.blocking_reasons
    )
    assert "orphan_checkpoint_threads" in report.blocking_reasons


def test_stale_active_legacy_run_blocks_recovery_outside_usage_window() -> None:
    repository = DurableTransientPersistence()
    repository.start_run(
        thread_id="stale-active",
        task_key="0:stale",
        project_name="stale",
        root_index=0,
        task_text="old task",
        runtime_config={"workflow_family": "legacy_firmware_rollback"},
    )
    baseline = repository.get_runtime_observation_baseline()

    report = audit_runtime_retirement(
        repository,
        observed_at=baseline + timedelta(days=31),
        checkpoint_thread_ids={"stale-active"},
    )

    assert report.legacy_firmware_rollback_runs == 0
    assert report.rollback_usage_gate_candidate is True
    assert report.active_legacy_or_unclassified_runs == 1
    assert report.recovery_dependency_gate_candidate is False


def test_sqlite_observation_baseline_and_metadata_survive_reopen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "luxar.sqlite3"
    repository = SQLitePersistence(path)
    baseline = repository.get_runtime_observation_baseline()
    repository.start_run(
        thread_id="agent-run",
        task_key="0:blink",
        project_name="blink",
        root_index=0,
        task_text="build",
        runtime_config={
            "workflow_family": "supervisor_firmware",
            "firmware_runtime": "supervisor",
        },
    )

    reopened = SQLitePersistence(path)
    observations = reopened.list_runtime_observations(since=baseline)

    assert reopened.get_runtime_observation_baseline() == baseline
    assert len(observations) == 1
    assert observations[0].workflow_family == "supervisor_firmware"
    assert observations[0].status == "running"


def test_checkpoint_inventory_reads_only_thread_identifiers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "checkpoints.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE checkpoints "
            "(thread_id TEXT, checkpoint BLOB, metadata BLOB)"
        )
        connection.executemany(
            "INSERT INTO checkpoints VALUES (?, ?, ?)",
            [
                ("thread-a", b"secret-a", b"metadata-a"),
                ("thread-a", b"secret-b", b"metadata-b"),
                ("thread-b", b"secret-c", b"metadata-c"),
            ],
        )

    assert inspect_sqlite_checkpoint_threads(path) == {
        "thread-a",
        "thread-b",
    }
