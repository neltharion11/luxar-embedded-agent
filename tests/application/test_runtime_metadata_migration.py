from __future__ import annotations

import sqlite3
from pathlib import Path

from luxar.application.runtime_metadata_migration import (
    plan_sqlite_runtime_metadata_migration,
)
from luxar.database import PendingApprovalRecord, SQLitePersistence


def _run(
    repository: SQLitePersistence,
    thread_id: str,
    *,
    runtime_config: dict[str, object],
    result: dict[str, object] | None,
) -> None:
    repository.start_run(
        thread_id=thread_id,
        task_key=f"0:{thread_id}",
        project_name=thread_id,
        root_index=0,
        task_text="private task text",
        runtime_config=runtime_config,
    )
    if result is not None:
        repository.finish_run(thread_id, status="completed", result=result)


def _runtime_configs(path: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT thread_id, runtime_config FROM luxar_workflow_runs "
            "ORDER BY thread_id"
        ).fetchall()


def test_migration_plan_classifies_only_deterministic_historical_records(
    tmp_path: Path,
) -> None:
    path = tmp_path / "luxar.sqlite3"
    repository = SQLitePersistence(path)
    _run(
        repository,
        "already",
        runtime_config={"workflow_family": "supervisor_firmware"},
        result={},
    )
    _run(
        repository,
        "old-supervisor",
        runtime_config={"agent_runtime": "supervisor"},
        result={},
    )
    _run(
        repository,
        "old-knowledge",
        runtime_config={"agent_runtime": "legacy"},
        result={"knowledge_result": {"matches": []}, "trace": []},
    )
    _run(
        repository,
        "old-inspection",
        runtime_config={"agent_runtime": "legacy"},
        result={"trace": ["analyze_project", "report_project"]},
    )
    _run(
        repository,
        "old-firmware",
        runtime_config={"agent_runtime": "legacy"},
        result={"trace": ["analyze_requirement", "create_plan"]},
    )
    _run(
        repository,
        "ambiguous",
        runtime_config={"agent_runtime": "legacy"},
        result={"status": "failed", "trace": ["failed"]},
    )
    _run(
        repository,
        "pending-knowledge",
        runtime_config={"agent_runtime": "legacy"},
        result=None,
    )
    repository.save_pending_approval(
        PendingApprovalRecord(
            task_key="0:pending-knowledge",
            project_name="pending-knowledge",
            root_index=0,
            thread_id="pending-knowledge",
            request={
                "kind": "knowledge_write",
                "title": "确认知识库变更",
            },
            runtime_config={"agent_runtime": "legacy"},
        )
    )
    before = _runtime_configs(path)

    first = plan_sqlite_runtime_metadata_migration(path)
    second = plan_sqlite_runtime_metadata_migration(path)

    assert first.total_runs == 7
    assert first.already_classified_runs == 1
    assert first.deterministic_candidates == 5
    assert first.ambiguous_runs == 1
    assert first.pending_approval_candidates == 1
    assert first.candidates_by_family == {
        "knowledge_task": 2,
        "legacy_firmware_rollback": 1,
        "project_inspection": 1,
        "supervisor_firmware": 1,
    }
    assert first.writes_performed == 0
    assert first.plan_id == second.plan_id
    assert _runtime_configs(path) == before


def test_migration_plan_does_not_expose_thread_or_task_text(
    tmp_path: Path,
) -> None:
    path = tmp_path / "luxar.sqlite3"
    repository = SQLitePersistence(path)
    _run(
        repository,
        "sensitive-thread-id",
        runtime_config={"agent_runtime": "supervisor"},
        result={},
    )

    payload = plan_sqlite_runtime_metadata_migration(path).model_dump_json()

    assert "sensitive-thread-id" not in payload
    assert "private task text" not in payload
