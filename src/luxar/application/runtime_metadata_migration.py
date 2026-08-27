"""Read-only migration planning for pre-``workflow_family`` SQLite records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


MigrationFamily = Literal[
    "supervisor_firmware",
    "legacy_firmware_rollback",
    "project_inspection",
    "knowledge_task",
]
_KNOWN_FAMILIES = {
    "supervisor_firmware",
    "legacy_firmware_rollback",
    "project_inspection",
    "knowledge_task",
}
_FIRMWARE_TRACE_NODES = {
    "analyze_requirement",
    "create_plan",
    "execute_next_step",
    "create_project",
    "find_idf_examples",
    "implement_change",
    "build_project",
    "repair_project",
    "request_flash_approval",
    "flash_project",
    "monitor_project",
    "analyze_device_logs",
}


@dataclass(frozen=True)
class _MigrationCandidate:
    thread_id: str
    family: MigrationFamily
    reason: str
    has_pending_approval: bool


class RuntimeMetadataMigrationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    database: Literal["sqlite"] = "sqlite"
    mode: Literal["dry_run"] = "dry_run"
    total_runs: int = Field(ge=0)
    already_classified_runs: int = Field(ge=0)
    deterministic_candidates: int = Field(ge=0)
    ambiguous_runs: int = Field(ge=0)
    pending_approval_candidates: int = Field(ge=0)
    candidates_by_family: dict[str, int]
    candidates_by_reason: dict[str, int]
    writes_performed: int = 0
    plan_id: str


def _json_object(value: object) -> dict[str, object]:
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _trace(result: dict[str, object]) -> set[str]:
    raw = result.get("trace")
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str)}


def _classify(
    *,
    runtime_config: dict[str, object],
    result: dict[str, object],
    request: dict[str, object],
) -> tuple[MigrationFamily, str] | None:
    configured_family = _text(runtime_config.get("workflow_family"))
    if configured_family in _KNOWN_FAMILIES:
        return None
    if runtime_config.get("agent_runtime") == "supervisor":
        return "supervisor_firmware", "explicit_supervisor_runtime"

    request_kind = _text(request.get("kind"))
    if request_kind == "knowledge_write":
        return "knowledge_task", "knowledge_approval_kind"
    if "knowledge_task" in result or "knowledge_result" in result:
        return "knowledge_task", "knowledge_result_contract"

    trace = _trace(result)
    if "report_project" in trace and not trace.intersection(
        _FIRMWARE_TRACE_NODES
    ):
        return "project_inspection", "inspection_trace"
    if trace.intersection(_FIRMWARE_TRACE_NODES):
        return "legacy_firmware_rollback", "firmware_trace"

    if request_kind == "task_approval":
        return "supervisor_firmware", "supervisor_approval_kind"
    if request_kind in {"plan_review", "clarification", "repair_review"}:
        return "legacy_firmware_rollback", "firmware_interaction_kind"
    if "port" in request and "step_description" in request:
        return "legacy_firmware_rollback", "legacy_flash_approval_contract"

    firmware_result_fields = (
        "requirement",
        "plan",
        "build_evidence",
        "flash_evidence",
        "monitor_evidence",
        "device_diagnosis",
        "repair_plan",
        "implementation_plan",
    )
    if any(result.get(field) is not None for field in firmware_result_fields):
        return "legacy_firmware_rollback", "firmware_result_contract"
    changed = result.get("changed_files")
    if isinstance(changed, list) and changed:
        return "legacy_firmware_rollback", "firmware_changed_files"
    return None


def _read_candidates(
    application_path: Path,
) -> tuple[int, int, list[_MigrationCandidate], int]:
    path = application_path.expanduser().resolve()
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT runs.thread_id, runs.runtime_config, runs.result,
                   approvals.request,
                   CASE WHEN approvals.task_key IS NULL THEN 0 ELSE 1 END
                       AS has_pending_approval
            FROM luxar_workflow_runs AS runs
            LEFT JOIN luxar_approval_requests AS approvals
              ON approvals.thread_id = runs.thread_id
             AND approvals.status = 'pending'
            ORDER BY runs.created_at, runs.thread_id
            """
        ).fetchall()
    finally:
        connection.close()

    candidates: list[_MigrationCandidate] = []
    already_classified = 0
    ambiguous = 0
    for row in rows:
        config = _json_object(row["runtime_config"])
        if _text(config.get("workflow_family")) in _KNOWN_FAMILIES:
            already_classified += 1
            continue
        classified = _classify(
            runtime_config=config,
            result=_json_object(row["result"]),
            request=_json_object(row["request"]),
        )
        if classified is None:
            ambiguous += 1
            continue
        family, reason = classified
        candidates.append(
            _MigrationCandidate(
                thread_id=str(row["thread_id"]),
                family=family,
                reason=reason,
                has_pending_approval=bool(row["has_pending_approval"]),
            )
        )
    return len(rows), already_classified, candidates, ambiguous


def plan_sqlite_runtime_metadata_migration(
    application_path: Path,
) -> RuntimeMetadataMigrationPlan:
    total, classified, candidates, ambiguous = _read_candidates(
        application_path
    )
    family_counts = Counter(item.family for item in candidates)
    reason_counts = Counter(item.reason for item in candidates)
    digest_payload = [
        {
            "thread_id": item.thread_id,
            "family": item.family,
            "reason": item.reason,
            "pending": item.has_pending_approval,
        }
        for item in candidates
    ]
    digest = hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return RuntimeMetadataMigrationPlan(
        total_runs=total,
        already_classified_runs=classified,
        deterministic_candidates=len(candidates),
        ambiguous_runs=ambiguous,
        pending_approval_candidates=sum(
            item.has_pending_approval for item in candidates
        ),
        candidates_by_family=dict(sorted(family_counts.items())),
        candidates_by_reason=dict(sorted(reason_counts.items())),
        plan_id=f"runtime-metadata-plan:{digest}",
    )


__all__ = [
    "RuntimeMetadataMigrationPlan",
    "plan_sqlite_runtime_metadata_migration",
]
