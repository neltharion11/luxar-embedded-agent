"""Application persistence ports and PostgreSQL implementation.

LangGraph owns its checkpoint tables. These repositories deliberately keep
queryable application records in separate ``luxar_*`` tables.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from luxar.domain.knowledge_atoms import KnowledgeChunk
from luxar.domain.continuous_agent.failures import ContinuousAgentFailure
from luxar.domain.continuous_agent.tools import (
    ToolExecutionLedgerStatus,
    ToolExecutionRecord,
)


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class PendingApprovalRecord:
    task_key: str
    project_name: str
    root_index: int
    thread_id: str
    request: dict[str, object]
    runtime_config: dict[str, object]
    status: str = "pending"
    decision: bool | None = None


@dataclass(frozen=True)
class ProjectMemory:
    project_key: str
    memory_key: str
    memory_type: str
    value: dict[str, object]
    confidence: float
    source_thread_id: str | None = None


@dataclass(frozen=True)
class KnowledgeMatch:
    document_id: str
    title: str
    source_uri: str
    ordinal: int
    content: str
    score: float
    knowledge_id: str | None = None
    subject: str | None = None
    category: str | None = None
    source_pages: tuple[int, ...] = ()
    source_section: str | None = None
    applicable_conditions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class WorkflowRunRecord:
    thread_id: str
    task_key: str
    task_text: str
    status: str
    result: dict[str, object]
    workflow_family: str | None = None


@dataclass(frozen=True)
class RuntimeObservationRecord:
    """Sanitized runtime metadata used by legacy-retirement audits."""

    thread_id: str
    status: str
    workflow_family: str | None
    firmware_runtime: str | None
    agent_runtime: str | None
    created_at: datetime


@dataclass(frozen=True)
class PendingRuntimeApproval:
    thread_id: str
    workflow_family: str | None
    firmware_runtime: str | None
    agent_runtime: str | None


@dataclass(frozen=True)
class AgentProjectRecord:
    """可恢复的项目目标、变更集和能力快照。"""

    project_key: str
    objective: dict[str, object]
    change_set: dict[str, object]
    revision: int
    capabilities: list[dict[str, object]]
    snapshot: dict[str, object]


@dataclass(frozen=True)
class WorkbenchSnapshotRecord:
    """Latest project workbench view across workflow families."""

    project_key: str
    workflow_family: str
    thread_id: str
    snapshot: dict[str, object]
    updated_at: datetime


@dataclass(frozen=True)
class AgentInteractionRecord:
    """Agent 与用户/审批/计划修订交互的追加记录。"""

    interaction_id: str
    project_key: str
    objective_id: str | None
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True)
class ConversationStreamRecord:
    """Recoverable, in-progress assistant stream for one workflow run."""

    thread_id: str
    task_key: str
    user_message: str
    assistant_content: str
    status: str
    last_sequence: int
    last_event: str | None
    updated_at: datetime


@dataclass(frozen=True)
class ConversationStreamEventRecord:
    thread_id: str
    sequence: int
    event: str
    data: dict[str, object] | str


@dataclass(frozen=True)
class AgentSessionRecord:
    """Stable LangGraph conversation identity for one project chat."""

    session_id: str
    project_key: str
    status: str
    active_objective_id: str | None
    context_summary: str
    compaction_cursor: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AgentTurnRecord:
    """One idempotent user turn inside a durable Agent Session."""

    turn_id: str
    session_id: str
    client_turn_id: str
    status: str
    user_message: str
    assistant_message: str
    failure: dict[str, object] | None
    created_at: datetime
    updated_at: datetime


class PersistencePort(Protocol):
    durable: bool

    def health(self) -> bool: ...

    def create_agent_session(
        self,
        *,
        session_id: str,
        project_key: str,
    ) -> AgentSessionRecord: ...

    def get_agent_session(
        self,
        session_id: str,
    ) -> AgentSessionRecord | None: ...

    def get_active_agent_session(
        self,
        project_key: str,
    ) -> AgentSessionRecord | None: ...

    def archive_agent_session(self, session_id: str) -> bool: ...

    def update_agent_session_state(
        self,
        session_id: str,
        *,
        active_objective_id: str | None,
        context_summary: str,
        compaction_cursor: int,
    ) -> AgentSessionRecord: ...

    def start_agent_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        client_turn_id: str,
        user_message: str,
    ) -> AgentTurnRecord: ...

    def get_agent_turn(self, turn_id: str) -> AgentTurnRecord | None: ...

    def get_agent_turn_by_client_id(
        self,
        *,
        session_id: str,
        client_turn_id: str,
    ) -> AgentTurnRecord | None: ...

    def finish_agent_turn(
        self,
        turn_id: str,
        *,
        status: str,
        assistant_message: str = "",
        failure: dict[str, object] | None = None,
    ) -> None: ...

    def reserve_tool_execution(
        self,
        *,
        idempotency_key: str,
        session_id: str,
        turn_id: str,
        call_id: str,
        tool_name: str,
        arguments_fingerprint: str,
    ) -> tuple[ToolExecutionRecord, bool]: ...

    def finish_tool_execution(
        self,
        idempotency_key: str,
        *,
        status: ToolExecutionLedgerStatus,
        result: dict[str, object] | None = None,
        failure: ContinuousAgentFailure | None = None,
    ) -> ToolExecutionRecord: ...

    def get_messages(self, task_key: str) -> list[dict[str, str]]: ...

    def append_exchange(
        self,
        task_key: str,
        *,
        thread_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None: ...

    def reset_conversation(self, task_key: str) -> None: ...

    def start_conversation_stream(
        self,
        *,
        thread_id: str,
        task_key: str,
        user_message: str,
    ) -> None: ...

    def append_conversation_stream_event(
        self,
        thread_id: str,
        *,
        event: str,
        data: dict[str, object] | str,
    ) -> int: ...

    def get_active_conversation_stream(
        self,
        task_key: str,
    ) -> ConversationStreamRecord | None: ...

    def get_conversation_stream(
        self,
        thread_id: str,
    ) -> ConversationStreamRecord | None: ...

    def list_conversation_stream_events(
        self,
        thread_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[ConversationStreamEventRecord]: ...

    def finish_conversation_stream(
        self,
        thread_id: str,
        *,
        status: str,
    ) -> None: ...

    def start_run(
        self,
        *,
        thread_id: str,
        task_key: str,
        project_name: str,
        root_index: int,
        task_text: str,
        runtime_config: dict[str, object],
    ) -> None: ...

    def finish_run(
        self,
        thread_id: str,
        *,
        status: str,
        result: dict[str, object] | None,
    ) -> None: ...

    def get_latest_completed_run(
        self,
        task_key: str,
    ) -> WorkflowRunRecord | None: ...

    def get_latest_run(self, task_key: str) -> WorkflowRunRecord | None: ...

    def get_runtime_observation_baseline(self) -> datetime: ...

    def list_runtime_observations(
        self,
        *,
        since: datetime,
    ) -> list[RuntimeObservationRecord]: ...

    def list_pending_runtime_approvals(
        self,
    ) -> list[PendingRuntimeApproval]: ...

    def save_pending_approval(
        self, record: PendingApprovalRecord
    ) -> None: ...

    def get_pending_approval(
        self, task_key: str
    ) -> PendingApprovalRecord | None: ...

    def decide_approval(self, task_key: str, approved: bool) -> bool: ...

    def complete_approval(self, task_key: str, *, failed: bool = False) -> None: ...

    def upsert_memory(
        self,
        *,
        project_key: str,
        memory_key: str,
        memory_type: str,
        value: dict[str, object],
        confidence: float = 1.0,
        source_thread_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> None: ...

    def find_memories(
        self,
        project_key: str,
        *,
        memory_type: str | None = None,
        limit: int = 20,
    ) -> list[ProjectMemory]: ...

    def replace_document(
        self,
        *,
        document_id: str,
        project_key: str,
        source_uri: str,
        title: str,
        content_hash: str,
        metadata: dict[str, object],
        chunks: list[KnowledgeChunk | tuple[str, int, list[float]]],
    ) -> None: ...

    def search_knowledge(
        self,
        *,
        project_key: str,
        query_text: str,
        query_embedding: list[float],
        limit: int = 6,
    ) -> list[KnowledgeMatch]: ...

    def count_knowledge_documents(self, project_key: str) -> int: ...

    def save_agent_project(
        self,
        *,
        project_key: str,
        objective: dict[str, object],
        change_set: dict[str, object],
        revision: int,
        capabilities: list[dict[str, object]],
        snapshot: dict[str, object] | None = None,
    ) -> None: ...

    def get_agent_project(self, project_key: str) -> AgentProjectRecord | None: ...

    def save_workbench_snapshot(
        self,
        *,
        project_key: str,
        workflow_family: str,
        thread_id: str,
        snapshot: dict[str, object],
    ) -> None: ...

    def get_workbench_snapshot(
        self,
        project_key: str,
    ) -> WorkbenchSnapshotRecord | None: ...

    def append_agent_interaction(
        self,
        *,
        interaction_id: str,
        project_key: str,
        objective_id: str | None,
        kind: str,
        payload: dict[str, object],
    ) -> None: ...

    def get_agent_interactions(
        self,
        project_key: str,
        *,
        limit: int = 100,
    ) -> list[AgentInteractionRecord]: ...


class TransientPersistence:
    """Thread-safe local fallback used when DATABASE_URL is absent.

    It keeps development and unit tests usable, while the Web response clearly
    reports ``durable=false``. Production durability uses PostgresPersistence.
    """

    durable = False

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._messages: dict[str, list[dict[str, str]]] = {}
        self._runs: dict[str, dict[str, object]] = {}
        self._approvals: dict[str, PendingApprovalRecord] = {}
        self._memories: dict[tuple[str, str], ProjectMemory] = {}
        self._agent_projects: dict[str, AgentProjectRecord] = {}
        self._workbench_snapshots: dict[str, WorkbenchSnapshotRecord] = {}
        self._agent_interactions: dict[str, AgentInteractionRecord] = {}
        self._conversation_streams: dict[str, dict[str, object]] = {}
        self._conversation_stream_events: dict[
            str, list[ConversationStreamEventRecord]
        ] = {}
        self._agent_sessions: dict[str, AgentSessionRecord] = {}
        self._agent_turns: dict[str, AgentTurnRecord] = {}
        self._agent_turn_client_ids: dict[tuple[str, str], str] = {}
        self._tool_executions: dict[str, ToolExecutionRecord] = {}
        self._runtime_observation_baseline = datetime.now(timezone.utc)

    def health(self) -> bool:
        return True

    def create_agent_session(
        self,
        *,
        session_id: str,
        project_key: str,
    ) -> AgentSessionRecord:
        if not session_id.strip() or not project_key.strip():
            raise ValueError("Agent Session 标识不能为空")
        with self._lock:
            existing = self._agent_sessions.get(session_id)
            if existing is not None:
                if existing.project_key != project_key:
                    raise ValueError("Agent Session 已属于其他项目")
                return existing
            now = datetime.now(timezone.utc)
            record = AgentSessionRecord(
                session_id=session_id,
                project_key=project_key,
                status="active",
                active_objective_id=None,
                context_summary="",
                compaction_cursor=0,
                created_at=now,
                updated_at=now,
            )
            self._agent_sessions[session_id] = record
            return record

    def get_agent_session(
        self,
        session_id: str,
    ) -> AgentSessionRecord | None:
        with self._lock:
            return self._agent_sessions.get(session_id)

    def get_active_agent_session(
        self,
        project_key: str,
    ) -> AgentSessionRecord | None:
        with self._lock:
            candidates = [
                record
                for record in self._agent_sessions.values()
                if record.project_key == project_key and record.status == "active"
            ]
        return max(candidates, key=lambda item: item.updated_at, default=None)

    def archive_agent_session(self, session_id: str) -> bool:
        with self._lock:
            record = self._agent_sessions.get(session_id)
            if record is None:
                return False
            self._agent_sessions[session_id] = AgentSessionRecord(
                **{
                    **record.__dict__,
                    "status": "archived",
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            return True

    def update_agent_session_state(
        self,
        session_id: str,
        *,
        active_objective_id: str | None,
        context_summary: str,
        compaction_cursor: int,
    ) -> AgentSessionRecord:
        if compaction_cursor < 0:
            raise ValueError("compaction_cursor 不能为负数")
        with self._lock:
            record = self._agent_sessions.get(session_id)
            if record is None:
                raise KeyError("Agent Session 不存在")
            updated = AgentSessionRecord(
                **{
                    **record.__dict__,
                    "active_objective_id": active_objective_id,
                    "context_summary": context_summary,
                    "compaction_cursor": compaction_cursor,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            self._agent_sessions[session_id] = updated
            return updated

    def start_agent_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        client_turn_id: str,
        user_message: str,
    ) -> AgentTurnRecord:
        if not all(
            value.strip()
            for value in (turn_id, session_id, client_turn_id, user_message)
        ):
            raise ValueError("Agent Turn 标识和消息不能为空")
        with self._lock:
            session = self._agent_sessions.get(session_id)
            if session is None or session.status != "active":
                raise ValueError("Agent Session 不存在或已归档")
            idempotency_key = (session_id, client_turn_id)
            existing_turn_id = self._agent_turn_client_ids.get(idempotency_key)
            if existing_turn_id is not None:
                return self._agent_turns[existing_turn_id]
            existing = self._agent_turns.get(turn_id)
            if existing is not None:
                if (
                    existing.session_id != session_id
                    or existing.client_turn_id != client_turn_id
                ):
                    raise ValueError("Agent Turn 标识冲突")
                return existing
            now = datetime.now(timezone.utc)
            record = AgentTurnRecord(
                turn_id=turn_id,
                session_id=session_id,
                client_turn_id=client_turn_id,
                status="running",
                user_message=user_message,
                assistant_message="",
                failure=None,
                created_at=now,
                updated_at=now,
            )
            self._agent_turns[turn_id] = record
            self._agent_turn_client_ids[idempotency_key] = turn_id
            self._agent_sessions[session_id] = AgentSessionRecord(
                **{**session.__dict__, "updated_at": now}
            )
            return record

    def get_agent_turn(self, turn_id: str) -> AgentTurnRecord | None:
        with self._lock:
            return self._agent_turns.get(turn_id)

    def get_agent_turn_by_client_id(
        self,
        *,
        session_id: str,
        client_turn_id: str,
    ) -> AgentTurnRecord | None:
        with self._lock:
            turn_id = self._agent_turn_client_ids.get(
                (session_id, client_turn_id)
            )
            return self._agent_turns.get(turn_id) if turn_id is not None else None

    def finish_agent_turn(
        self,
        turn_id: str,
        *,
        status: str,
        assistant_message: str = "",
        failure: dict[str, object] | None = None,
    ) -> None:
        allowed_statuses = {
            "running",
            "waiting_input",
            "waiting_approval",
            "completed",
            "failed",
            "cancelled",
        }
        if status not in allowed_statuses:
            raise ValueError("Agent Turn 状态无效")
        with self._lock:
            record = self._agent_turns.get(turn_id)
            if record is None:
                raise KeyError("Agent Turn 不存在")
            now = datetime.now(timezone.utc)
            copied_failure = (
                json.loads(json.dumps(failure, ensure_ascii=False))
                if failure is not None
                else None
            )
            self._agent_turns[turn_id] = AgentTurnRecord(
                **{
                    **record.__dict__,
                    "status": status,
                    "assistant_message": assistant_message,
                    "failure": copied_failure,
                    "updated_at": now,
                }
            )
            session = self._agent_sessions.get(record.session_id)
            if session is not None:
                self._agent_sessions[record.session_id] = AgentSessionRecord(
                    **{**session.__dict__, "updated_at": now}
                )

    def reserve_tool_execution(
        self,
        *,
        idempotency_key: str,
        session_id: str,
        turn_id: str,
        call_id: str,
        tool_name: str,
        arguments_fingerprint: str,
    ) -> tuple[ToolExecutionRecord, bool]:
        with self._lock:
            existing = self._tool_executions.get(idempotency_key)
            if existing is not None:
                if (
                    existing.session_id != session_id
                    or existing.turn_id != turn_id
                    or existing.call_id != call_id
                    or existing.tool_name != tool_name
                    or existing.arguments_fingerprint != arguments_fingerprint
                ):
                    raise ValueError("Tool execution idempotency conflict")
                return existing, False
            turn = self._agent_turns.get(turn_id)
            if turn is None or turn.session_id != session_id:
                raise ValueError("Tool execution Turn 不存在或不属于 Session")
            record = ToolExecutionRecord(
                idempotency_key=idempotency_key,
                session_id=session_id,
                turn_id=turn_id,
                call_id=call_id,
                tool_name=tool_name,
                arguments_fingerprint=arguments_fingerprint,
                status="running",
            )
            self._tool_executions[idempotency_key] = record
            return record, True

    def finish_tool_execution(
        self,
        idempotency_key: str,
        *,
        status: ToolExecutionLedgerStatus,
        result: dict[str, object] | None = None,
        failure: ContinuousAgentFailure | None = None,
    ) -> ToolExecutionRecord:
        if status == "running":
            raise ValueError("Tool execution 结束状态不能是 running")
        with self._lock:
            record = self._tool_executions.get(idempotency_key)
            if record is None:
                raise KeyError("Tool execution 不存在")
            updated = record.model_copy(
                update={
                    "status": status,
                    "result": (
                        json.loads(json.dumps(result, ensure_ascii=False))
                        if result is not None
                        else None
                    ),
                    "failure": failure,
                }
            )
            self._tool_executions[idempotency_key] = updated
            return updated

    def get_messages(self, task_key: str) -> list[dict[str, str]]:
        with self._lock:
            return [dict(item) for item in self._messages.get(task_key, [])]

    def append_exchange(
        self,
        task_key: str,
        *,
        thread_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        del thread_id
        with self._lock:
            self._messages.setdefault(task_key, []).extend(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_message},
                ]
            )

    def reset_conversation(self, task_key: str) -> None:
        with self._lock:
            self._messages.pop(task_key, None)
            thread_ids = [
                thread_id
                for thread_id, stream in self._conversation_streams.items()
                if stream.get("task_key") == task_key
            ]
            for thread_id in thread_ids:
                self._conversation_streams.pop(thread_id, None)
                self._conversation_stream_events.pop(thread_id, None)

    def start_conversation_stream(
        self,
        *,
        thread_id: str,
        task_key: str,
        user_message: str,
    ) -> None:
        with self._lock:
            for existing_id, stream in list(self._conversation_streams.items()):
                if (
                    stream.get("task_key") == task_key
                    and stream.get("status") not in {"running", "pending_approval"}
                ):
                    self._conversation_streams.pop(existing_id, None)
                    self._conversation_stream_events.pop(existing_id, None)
            self._conversation_streams[thread_id] = {
                "thread_id": thread_id,
                "task_key": task_key,
                "user_message": user_message,
                "assistant_content": "",
                "status": "running",
                "last_sequence": 0,
                "last_event": None,
                "updated_at": datetime.now(timezone.utc),
            }
            self._conversation_stream_events[thread_id] = []

    def append_conversation_stream_event(
        self,
        thread_id: str,
        *,
        event: str,
        data: dict[str, object] | str,
    ) -> int:
        with self._lock:
            stream = self._conversation_streams.get(thread_id)
            if stream is None:
                raise KeyError("conversation stream does not exist")
            sequence = int(stream.get("last_sequence", 0)) + 1
            stored_data = dict(data) if isinstance(data, dict) else data
            self._conversation_stream_events.setdefault(thread_id, []).append(
                ConversationStreamEventRecord(
                    thread_id=thread_id,
                    sequence=sequence,
                    event=event,
                    data=stored_data,
                )
            )
            content = str(stream.get("assistant_content", ""))
            if event == "token" and isinstance(data, dict):
                content += str(data.get("token", data.get("content", "")))
            elif event == "reset_output":
                content = ""
            stream.update(
                {
                    "assistant_content": content,
                    "last_sequence": sequence,
                    "last_event": event,
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            return sequence

    @staticmethod
    def _stream_record(values: dict[str, object]) -> ConversationStreamRecord:
        updated_at = values.get("updated_at")
        if not isinstance(updated_at, datetime):
            updated_at = datetime.now(timezone.utc)
        return ConversationStreamRecord(
            thread_id=str(values.get("thread_id", "")),
            task_key=str(values.get("task_key", "")),
            user_message=str(values.get("user_message", "")),
            assistant_content=str(values.get("assistant_content", "")),
            status=str(values.get("status", "running")),
            last_sequence=int(values.get("last_sequence", 0)),
            last_event=_optional_text(values.get("last_event")),
            updated_at=updated_at,
        )

    def get_active_conversation_stream(
        self,
        task_key: str,
    ) -> ConversationStreamRecord | None:
        with self._lock:
            for values in reversed(list(self._conversation_streams.values())):
                if values.get("task_key") == task_key and values.get("status") in {
                    "running",
                    "pending_approval",
                }:
                    return self._stream_record(values)
        return None

    def get_conversation_stream(
        self,
        thread_id: str,
    ) -> ConversationStreamRecord | None:
        with self._lock:
            values = self._conversation_streams.get(thread_id)
            return self._stream_record(values) if values is not None else None

    def list_conversation_stream_events(
        self,
        thread_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[ConversationStreamEventRecord]:
        if after_sequence < 0 or not 1 <= limit <= 2000:
            raise ValueError("conversation stream event range is invalid")
        with self._lock:
            return [
                ConversationStreamEventRecord(
                    thread_id=item.thread_id,
                    sequence=item.sequence,
                    event=item.event,
                    data=dict(item.data) if isinstance(item.data, dict) else item.data,
                )
                for item in self._conversation_stream_events.get(thread_id, [])
                if item.sequence > after_sequence
            ][:limit]

    def finish_conversation_stream(
        self,
        thread_id: str,
        *,
        status: str,
    ) -> None:
        with self._lock:
            stream = self._conversation_streams.get(thread_id)
            if stream is not None:
                stream.update(
                    {"status": status, "updated_at": datetime.now(timezone.utc)}
                )

    def start_run(self, **values: object) -> None:
        with self._lock:
            stored = dict(values)
            stored.setdefault("status", "running")
            stored.setdefault("created_at", datetime.now(timezone.utc))
            self._runs[str(values["thread_id"])] = stored

    def finish_run(
        self,
        thread_id: str,
        *,
        status: str,
        result: dict[str, object] | None,
    ) -> None:
        with self._lock:
            self._runs.setdefault(thread_id, {}).update(
                {"status": status, "result": result}
            )

    def get_latest_completed_run(
        self,
        task_key: str,
    ) -> WorkflowRunRecord | None:
        with self._lock:
            for values in reversed(list(self._runs.values())):
                result = values.get("result")
                if (
                    values.get("task_key") == task_key
                    and values.get("status") == "completed"
                    and isinstance(result, dict)
                ):
                    return WorkflowRunRecord(
                        thread_id=str(values.get("thread_id", "")),
                        task_key=task_key,
                        task_text=str(values.get("task_text", "")),
                        status="completed",
                        result=dict(result),
                    )
        return None

    def get_latest_run(self, task_key: str) -> WorkflowRunRecord | None:
        with self._lock:
            for values in reversed(list(self._runs.values())):
                result = values.get("result")
                if values.get("task_key") == task_key:
                    config = values.get("runtime_config")
                    runtime_config = config if isinstance(config, dict) else {}
                    return WorkflowRunRecord(
                        thread_id=str(values.get("thread_id", "")),
                        task_key=task_key,
                        task_text=str(values.get("task_text", "")),
                        status=str(values.get("status", "failed")),
                        result=dict(result) if isinstance(result, dict) else {},
                        workflow_family=_optional_text(
                            runtime_config.get("workflow_family")
                        ),
                    )
        return None

    def get_runtime_observation_baseline(self) -> datetime:
        return self._runtime_observation_baseline

    def list_runtime_observations(
        self,
        *,
        since: datetime,
    ) -> list[RuntimeObservationRecord]:
        with self._lock:
            values = list(self._runs.values())
        observations: list[RuntimeObservationRecord] = []
        for item in values:
            created_at = item.get("created_at")
            if not isinstance(created_at, datetime) or created_at < since:
                continue
            config = item.get("runtime_config")
            runtime_config = config if isinstance(config, dict) else {}
            observations.append(
                RuntimeObservationRecord(
                    thread_id=str(item.get("thread_id", "")),
                    status=str(item.get("status", "running")),
                    workflow_family=_optional_text(
                        runtime_config.get("workflow_family")
                    ),
                    firmware_runtime=_optional_text(
                        runtime_config.get("firmware_runtime")
                    ),
                    agent_runtime=_optional_text(
                        runtime_config.get("agent_runtime")
                    ),
                    created_at=created_at,
                )
            )
        return observations

    def list_pending_runtime_approvals(
        self,
    ) -> list[PendingRuntimeApproval]:
        with self._lock:
            records = [
                item
                for item in self._approvals.values()
                if item.status == "pending"
            ]
        return [
            PendingRuntimeApproval(
                thread_id=item.thread_id,
                workflow_family=_optional_text(
                    item.runtime_config.get("workflow_family")
                ),
                firmware_runtime=_optional_text(
                    item.runtime_config.get("firmware_runtime")
                ),
                agent_runtime=_optional_text(
                    item.runtime_config.get("agent_runtime")
                ),
            )
            for item in records
        ]

    def save_pending_approval(self, record: PendingApprovalRecord) -> None:
        with self._lock:
            self._approvals[record.task_key] = record

    def get_pending_approval(
        self, task_key: str
    ) -> PendingApprovalRecord | None:
        with self._lock:
            record = self._approvals.get(task_key)
            return record if record and record.status == "pending" else None

    def decide_approval(self, task_key: str, approved: bool) -> bool:
        with self._lock:
            record = self._approvals.get(task_key)
            if record is None or record.status != "pending":
                return False
            self._approvals[task_key] = PendingApprovalRecord(
                **{
                    **record.__dict__,
                    "status": "decided",
                    "decision": approved,
                }
            )
            return True

    def complete_approval(self, task_key: str, *, failed: bool = False) -> None:
        with self._lock:
            record = self._approvals.get(task_key)
            if record is None:
                return
            self._approvals[task_key] = PendingApprovalRecord(
                **{
                    **record.__dict__,
                    "status": "failed" if failed else "completed",
                }
            )

    def upsert_memory(
        self,
        *,
        project_key: str,
        memory_key: str,
        memory_type: str,
        value: dict[str, object],
        confidence: float = 1.0,
        source_thread_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        del expires_at
        if not 0 <= confidence <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")
        with self._lock:
            self._memories[(project_key, memory_key)] = ProjectMemory(
                project_key=project_key,
                memory_key=memory_key,
                memory_type=memory_type,
                value=dict(value),
                confidence=confidence,
                source_thread_id=source_thread_id,
            )

    def find_memories(
        self,
        project_key: str,
        *,
        memory_type: str | None = None,
        limit: int = 20,
    ) -> list[ProjectMemory]:
        with self._lock:
            values = [
                item
                for item in self._memories.values()
                if item.project_key == project_key
                and (memory_type is None or item.memory_type == memory_type)
            ]
        return values[:limit]

    def replace_document(self, **_: object) -> None:
        raise RuntimeError("外部知识库需要 PostgreSQL + pgvector")

    def search_knowledge(self, **_: object) -> list[KnowledgeMatch]:
        return []

    def count_knowledge_documents(self, project_key: str) -> int:
        return 0

    def save_agent_project(
        self,
        *,
        project_key: str,
        objective: dict[str, object],
        change_set: dict[str, object],
        revision: int,
        capabilities: list[dict[str, object]],
        snapshot: dict[str, object] | None = None,
    ) -> None:
        if not project_key.strip() or revision < 1:
            raise ValueError("agent project key 和 revision 无效")
        with self._lock:
            self._agent_projects[project_key] = AgentProjectRecord(
                project_key=project_key,
                objective=json.loads(json.dumps(objective, ensure_ascii=False)),
                change_set=json.loads(json.dumps(change_set, ensure_ascii=False)),
                revision=revision,
                capabilities=json.loads(json.dumps(capabilities, ensure_ascii=False)),
                snapshot=json.loads(json.dumps(snapshot or {}, ensure_ascii=False)),
            )

    def get_agent_project(self, project_key: str) -> AgentProjectRecord | None:
        with self._lock:
            record = self._agent_projects.get(project_key)
            if record is None:
                return None
            return AgentProjectRecord(
                project_key=record.project_key,
                objective=json.loads(json.dumps(record.objective, ensure_ascii=False)),
                change_set=json.loads(json.dumps(record.change_set, ensure_ascii=False)),
                revision=record.revision,
                capabilities=json.loads(json.dumps(record.capabilities, ensure_ascii=False)),
                snapshot=json.loads(json.dumps(record.snapshot, ensure_ascii=False)),
            )

    def save_workbench_snapshot(
        self,
        *,
        project_key: str,
        workflow_family: str,
        thread_id: str,
        snapshot: dict[str, object],
    ) -> None:
        if (
            not project_key.strip()
            or not workflow_family.strip()
            or not thread_id.strip()
        ):
            raise ValueError("workbench snapshot 标识不能为空")
        with self._lock:
            self._workbench_snapshots[project_key] = WorkbenchSnapshotRecord(
                project_key=project_key,
                workflow_family=workflow_family,
                thread_id=thread_id,
                snapshot=json.loads(json.dumps(snapshot, ensure_ascii=False)),
                updated_at=datetime.now(timezone.utc),
            )

    def get_workbench_snapshot(
        self,
        project_key: str,
    ) -> WorkbenchSnapshotRecord | None:
        with self._lock:
            record = self._workbench_snapshots.get(project_key)
            if record is None:
                return None
            return WorkbenchSnapshotRecord(
                project_key=record.project_key,
                workflow_family=record.workflow_family,
                thread_id=record.thread_id,
                snapshot=json.loads(json.dumps(record.snapshot, ensure_ascii=False)),
                updated_at=record.updated_at,
            )

    def append_agent_interaction(
        self,
        *,
        interaction_id: str,
        project_key: str,
        objective_id: str | None,
        kind: str,
        payload: dict[str, object],
    ) -> None:
        if not interaction_id.strip() or not project_key.strip() or not kind.strip():
            raise ValueError("agent interaction 标识不能为空")
        with self._lock:
            self._agent_interactions.setdefault(
                interaction_id,
                AgentInteractionRecord(
                    interaction_id=interaction_id,
                    project_key=project_key,
                    objective_id=objective_id,
                    kind=kind,
                    payload=json.loads(json.dumps(payload, ensure_ascii=False)),
                ),
            )

    def get_agent_interactions(
        self,
        project_key: str,
        *,
        limit: int = 100,
    ) -> list[AgentInteractionRecord]:
        if not 1 <= limit <= 500:
            raise ValueError("limit 必须在 1 到 500 之间")
        with self._lock:
            values = [
                record
                for record in self._agent_interactions.values()
                if record.project_key == project_key
            ]
        return values[-limit:]


class PostgresPersistence:
    durable = True

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def health(self) -> bool:
        try:
            with self._pool.connection() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except Exception:
            return False

    @staticmethod
    def _postgres_timestamp(value: object) -> datetime:
        if not isinstance(value, datetime):
            raise RuntimeError("Agent persistence timestamp is invalid")
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

    @classmethod
    def _postgres_agent_session(cls, row: object) -> AgentSessionRecord:
        values = row
        return AgentSessionRecord(
            session_id=values[0],  # type: ignore[index]
            project_key=values[1],  # type: ignore[index]
            status=values[2],  # type: ignore[index]
            active_objective_id=values[3],  # type: ignore[index]
            context_summary=values[4],  # type: ignore[index]
            compaction_cursor=int(values[5]),  # type: ignore[index]
            created_at=cls._postgres_timestamp(values[6]),  # type: ignore[index]
            updated_at=cls._postgres_timestamp(values[7]),  # type: ignore[index]
        )

    @classmethod
    def _postgres_agent_turn(cls, row: object) -> AgentTurnRecord:
        values = row
        stored_failure = values[6]  # type: ignore[index]
        if isinstance(stored_failure, str):
            stored_failure = json.loads(stored_failure)
        return AgentTurnRecord(
            turn_id=values[0],  # type: ignore[index]
            session_id=values[1],  # type: ignore[index]
            client_turn_id=values[2],  # type: ignore[index]
            status=values[3],  # type: ignore[index]
            user_message=values[4],  # type: ignore[index]
            assistant_message=values[5],  # type: ignore[index]
            failure=(
                dict(stored_failure)
                if isinstance(stored_failure, dict)
                else None
            ),
            created_at=cls._postgres_timestamp(values[7]),  # type: ignore[index]
            updated_at=cls._postgres_timestamp(values[8]),  # type: ignore[index]
        )

    def create_agent_session(
        self,
        *,
        session_id: str,
        project_key: str,
    ) -> AgentSessionRecord:
        if not session_id.strip() or not project_key.strip():
            raise ValueError("Agent Session 标识不能为空")
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO luxar_agent_sessions (session_id, project_key)
                    VALUES (%s, %s)
                    ON CONFLICT (session_id) DO NOTHING
                    """,
                    (session_id, project_key),
                )
                row = connection.execute(
                    """
                    SELECT session_id, project_key, status, active_objective_id,
                           context_summary, compaction_cursor,
                           created_at, updated_at
                    FROM luxar_agent_sessions WHERE session_id = %s
                    """,
                    (session_id,),
                ).fetchone()
        if row is None:
            raise RuntimeError("Agent Session 创建失败")
        record = self._postgres_agent_session(row)
        if record.project_key != project_key:
            raise ValueError("Agent Session 已属于其他项目")
        return record

    def get_agent_session(
        self,
        session_id: str,
    ) -> AgentSessionRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT session_id, project_key, status, active_objective_id,
                       context_summary, compaction_cursor,
                       created_at, updated_at
                FROM luxar_agent_sessions WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
        return self._postgres_agent_session(row) if row is not None else None

    def get_active_agent_session(
        self,
        project_key: str,
    ) -> AgentSessionRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT session_id, project_key, status, active_objective_id,
                       context_summary, compaction_cursor,
                       created_at, updated_at
                FROM luxar_agent_sessions
                WHERE project_key = %s AND status = 'active'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (project_key,),
            ).fetchone()
        return self._postgres_agent_session(row) if row is not None else None

    def archive_agent_session(self, session_id: str) -> bool:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                UPDATE luxar_agent_sessions
                SET status = 'archived', updated_at = now()
                WHERE session_id = %s
                RETURNING session_id
                """,
                (session_id,),
            ).fetchone()
        return row is not None

    def update_agent_session_state(
        self,
        session_id: str,
        *,
        active_objective_id: str | None,
        context_summary: str,
        compaction_cursor: int,
    ) -> AgentSessionRecord:
        if compaction_cursor < 0:
            raise ValueError("compaction_cursor 不能为负数")
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                UPDATE luxar_agent_sessions
                SET active_objective_id = %s, context_summary = %s,
                    compaction_cursor = %s, updated_at = now()
                WHERE session_id = %s
                RETURNING session_id, project_key, status, active_objective_id,
                          context_summary, compaction_cursor,
                          created_at, updated_at
                """,
                (
                    active_objective_id,
                    context_summary,
                    compaction_cursor,
                    session_id,
                ),
            ).fetchone()
        if row is None:
            raise KeyError("Agent Session 不存在")
        return self._postgres_agent_session(row)

    def start_agent_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        client_turn_id: str,
        user_message: str,
    ) -> AgentTurnRecord:
        if not all(
            value.strip()
            for value in (turn_id, session_id, client_turn_id, user_message)
        ):
            raise ValueError("Agent Turn 标识和消息不能为空")
        with self._pool.connection() as connection:
            with connection.transaction():
                existing = connection.execute(
                    """
                    SELECT turn_id, session_id, client_turn_id, status,
                           user_message, assistant_message, failure,
                           created_at, updated_at
                    FROM luxar_agent_turns
                    WHERE session_id = %s AND client_turn_id = %s
                    """,
                    (session_id, client_turn_id),
                ).fetchone()
                if existing is not None:
                    return self._postgres_agent_turn(existing)
                session = connection.execute(
                    """
                    SELECT status FROM luxar_agent_sessions
                    WHERE session_id = %s
                    """,
                    (session_id,),
                ).fetchone()
                if session is None or session[0] != "active":
                    raise ValueError("Agent Session 不存在或已归档")
                connection.execute(
                    """
                    INSERT INTO luxar_agent_turns
                        (turn_id, session_id, client_turn_id, user_message)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (session_id, client_turn_id) DO NOTHING
                    """,
                    (turn_id, session_id, client_turn_id, user_message),
                )
                connection.execute(
                    """
                    UPDATE luxar_agent_sessions SET updated_at = now()
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                row = connection.execute(
                    """
                    SELECT turn_id, session_id, client_turn_id, status,
                           user_message, assistant_message, failure,
                           created_at, updated_at
                    FROM luxar_agent_turns
                    WHERE session_id = %s AND client_turn_id = %s
                    """,
                    (session_id, client_turn_id),
                ).fetchone()
        if row is None:
            raise RuntimeError("Agent Turn 创建失败")
        return self._postgres_agent_turn(row)

    def get_agent_turn(self, turn_id: str) -> AgentTurnRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT turn_id, session_id, client_turn_id, status,
                       user_message, assistant_message, failure,
                       created_at, updated_at
                FROM luxar_agent_turns WHERE turn_id = %s
                """,
                (turn_id,),
            ).fetchone()
        return self._postgres_agent_turn(row) if row is not None else None

    def get_agent_turn_by_client_id(
        self,
        *,
        session_id: str,
        client_turn_id: str,
    ) -> AgentTurnRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT turn_id, session_id, client_turn_id, status,
                       user_message, assistant_message, failure,
                       created_at, updated_at
                FROM luxar_agent_turns
                WHERE session_id = %s AND client_turn_id = %s
                """,
                (session_id, client_turn_id),
            ).fetchone()
        return self._postgres_agent_turn(row) if row is not None else None

    def finish_agent_turn(
        self,
        turn_id: str,
        *,
        status: str,
        assistant_message: str = "",
        failure: dict[str, object] | None = None,
    ) -> None:
        allowed_statuses = {
            "running",
            "waiting_input",
            "waiting_approval",
            "completed",
            "failed",
            "cancelled",
        }
        if status not in allowed_statuses:
            raise ValueError("Agent Turn 状态无效")
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    """
                    UPDATE luxar_agent_turns
                    SET status = %s, assistant_message = %s,
                        failure = %s::jsonb, updated_at = now()
                    WHERE turn_id = %s
                    RETURNING session_id
                    """,
                    (
                        status,
                        assistant_message,
                        self._json(failure) if failure is not None else None,
                        turn_id,
                    ),
                ).fetchone()
                if row is None:
                    raise KeyError("Agent Turn 不存在")
                connection.execute(
                    """
                    UPDATE luxar_agent_sessions SET updated_at = now()
                    WHERE session_id = %s
                    """,
                    (row[0],),
                )

    @staticmethod
    def _postgres_tool_execution(row: object) -> ToolExecutionRecord:
        values = row
        raw_result = values[7]  # type: ignore[index]
        raw_failure = values[8]  # type: ignore[index]
        if isinstance(raw_result, str):
            raw_result = json.loads(raw_result)
        if isinstance(raw_failure, str):
            raw_failure = json.loads(raw_failure)
        return ToolExecutionRecord(
            idempotency_key=values[0],  # type: ignore[index]
            session_id=values[1],  # type: ignore[index]
            turn_id=values[2],  # type: ignore[index]
            call_id=values[3],  # type: ignore[index]
            tool_name=values[4],  # type: ignore[index]
            arguments_fingerprint=values[5],  # type: ignore[index]
            status=values[6],  # type: ignore[index]
            result=dict(raw_result) if isinstance(raw_result, dict) else None,
            failure=(
                ContinuousAgentFailure.model_validate(raw_failure)
                if raw_failure is not None
                else None
            ),
        )

    def reserve_tool_execution(
        self,
        *,
        idempotency_key: str,
        session_id: str,
        turn_id: str,
        call_id: str,
        tool_name: str,
        arguments_fingerprint: str,
    ) -> tuple[ToolExecutionRecord, bool]:
        with self._pool.connection() as connection:
            with connection.transaction():
                created_row = connection.execute(
                    """
                    INSERT INTO luxar_tool_executions
                        (idempotency_key, session_id, turn_id, call_id,
                         tool_name, arguments_fingerprint, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'running')
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING idempotency_key, session_id, turn_id, call_id,
                              tool_name, arguments_fingerprint, status,
                              result, failure
                    """,
                    (
                        idempotency_key,
                        session_id,
                        turn_id,
                        call_id,
                        tool_name,
                        arguments_fingerprint,
                    ),
                ).fetchone()
                row = created_row
                if row is None:
                    row = connection.execute(
                        """
                        SELECT idempotency_key, session_id, turn_id, call_id,
                               tool_name, arguments_fingerprint, status,
                               result, failure
                        FROM luxar_tool_executions
                        WHERE idempotency_key = %s
                        """,
                        (idempotency_key,),
                    ).fetchone()
        if row is None:
            raise RuntimeError("Tool execution reservation failed")
        record = self._postgres_tool_execution(row)
        if (
            record.session_id != session_id
            or record.turn_id != turn_id
            or record.call_id != call_id
            or record.tool_name != tool_name
            or record.arguments_fingerprint != arguments_fingerprint
        ):
            raise ValueError("Tool execution idempotency conflict")
        return record, created_row is not None

    def finish_tool_execution(
        self,
        idempotency_key: str,
        *,
        status: ToolExecutionLedgerStatus,
        result: dict[str, object] | None = None,
        failure: ContinuousAgentFailure | None = None,
    ) -> ToolExecutionRecord:
        if status == "running":
            raise ValueError("Tool execution 结束状态不能是 running")
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                UPDATE luxar_tool_executions
                SET status = %s, result = %s::jsonb, failure = %s::jsonb,
                    updated_at = now()
                WHERE idempotency_key = %s
                RETURNING idempotency_key, session_id, turn_id, call_id,
                          tool_name, arguments_fingerprint, status,
                          result, failure
                """,
                (
                    status,
                    self._json(result) if result is not None else None,
                    (
                        self._json(failure.model_dump(mode="json"))
                        if failure is not None
                        else None
                    ),
                    idempotency_key,
                ),
            ).fetchone()
        if row is None:
            raise KeyError("Tool execution 不存在")
        return self._postgres_tool_execution(row)

    def get_messages(self, task_key: str) -> list[dict[str, str]]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT role, content FROM luxar_conversation_messages
                WHERE task_key = %s ORDER BY id
                """,
                (task_key,),
            ).fetchall()
        return [{"role": row[0], "content": row[1]} for row in rows]

    def append_exchange(
        self,
        task_key: str,
        *,
        thread_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        with self._pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO luxar_conversation_messages
                            (task_key, role, content, thread_id)
                        VALUES (%s, %s, %s, %s)
                        """,
                        [
                            (task_key, "user", user_message, thread_id),
                            (task_key, "assistant", assistant_message, thread_id),
                        ],
                    )

    def reset_conversation(self, task_key: str) -> None:
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    "DELETE FROM luxar_conversation_messages WHERE task_key = %s",
                    (task_key,),
                )
                connection.execute(
                    "DELETE FROM luxar_conversation_streams WHERE task_key = %s",
                    (task_key,),
                )

    def start_conversation_stream(
        self,
        *,
        thread_id: str,
        task_key: str,
        user_message: str,
    ) -> None:
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    """
                    DELETE FROM luxar_conversation_streams
                    WHERE task_key = %s
                      AND status NOT IN ('running', 'pending_approval')
                    """,
                    (task_key,),
                )
                connection.execute(
                    """
                    INSERT INTO luxar_conversation_streams
                        (thread_id, task_key, user_message, assistant_content,
                         status, last_sequence)
                    VALUES (%s, %s, %s, '', 'running', 0)
                    ON CONFLICT (thread_id) DO UPDATE SET
                        task_key = EXCLUDED.task_key,
                        user_message = EXCLUDED.user_message,
                        assistant_content = '',
                        status = 'running',
                        last_sequence = 0,
                        last_event = NULL,
                        updated_at = now()
                    """,
                    (thread_id, task_key, user_message),
                )
                connection.execute(
                    "DELETE FROM luxar_conversation_stream_events "
                    "WHERE thread_id = %s",
                    (thread_id,),
                )

    def append_conversation_stream_event(
        self,
        thread_id: str,
        *,
        event: str,
        data: dict[str, object] | str,
    ) -> int:
        token = ""
        if event == "token" and isinstance(data, dict):
            token = str(data.get("token", data.get("content", "")))
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    """
                    UPDATE luxar_conversation_streams
                    SET last_sequence = last_sequence + 1,
                        last_event = %s,
                        assistant_content = CASE
                            WHEN %s = 'reset_output' THEN ''
                            WHEN %s = 'token' THEN assistant_content || %s
                            ELSE assistant_content
                        END,
                        updated_at = now()
                    WHERE thread_id = %s
                    RETURNING last_sequence
                    """,
                    (event, event, event, token, thread_id),
                ).fetchone()
                if row is None:
                    raise KeyError("conversation stream does not exist")
                sequence = int(row[0])
                connection.execute(
                    """
                    INSERT INTO luxar_conversation_stream_events
                        (thread_id, sequence, event, data)
                    VALUES (%s, %s, %s, %s::jsonb)
                    """,
                    (thread_id, sequence, event, self._json(data)),
                )
        return sequence

    @staticmethod
    def _postgres_stream_record(row: object) -> ConversationStreamRecord:
        values = row  # psycopg row supports positional access.
        updated_at = values[7]  # type: ignore[index]
        if not isinstance(updated_at, datetime):
            raise RuntimeError("conversation stream timestamp is invalid")
        return ConversationStreamRecord(
            thread_id=values[0],  # type: ignore[index]
            task_key=values[1],  # type: ignore[index]
            user_message=values[2],  # type: ignore[index]
            assistant_content=values[3],  # type: ignore[index]
            status=values[4],  # type: ignore[index]
            last_sequence=int(values[5]),  # type: ignore[index]
            last_event=values[6],  # type: ignore[index]
            updated_at=(
                updated_at.replace(tzinfo=timezone.utc)
                if updated_at.tzinfo is None
                else updated_at.astimezone(timezone.utc)
            ),
        )

    def get_active_conversation_stream(
        self,
        task_key: str,
    ) -> ConversationStreamRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT thread_id, task_key, user_message, assistant_content,
                       status, last_sequence, last_event, updated_at
                FROM luxar_conversation_streams
                WHERE task_key = %s
                  AND status IN ('running', 'pending_approval')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (task_key,),
            ).fetchone()
        return self._postgres_stream_record(row) if row is not None else None

    def get_conversation_stream(
        self,
        thread_id: str,
    ) -> ConversationStreamRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT thread_id, task_key, user_message, assistant_content,
                       status, last_sequence, last_event, updated_at
                FROM luxar_conversation_streams WHERE thread_id = %s
                """,
                (thread_id,),
            ).fetchone()
        return self._postgres_stream_record(row) if row is not None else None

    def list_conversation_stream_events(
        self,
        thread_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[ConversationStreamEventRecord]:
        if after_sequence < 0 or not 1 <= limit <= 2000:
            raise ValueError("conversation stream event range is invalid")
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT sequence, event, data
                FROM luxar_conversation_stream_events
                WHERE thread_id = %s AND sequence > %s
                ORDER BY sequence LIMIT %s
                """,
                (thread_id, after_sequence, limit),
            ).fetchall()
        return [
            ConversationStreamEventRecord(
                thread_id=thread_id,
                sequence=int(row[0]),
                event=row[1],
                data=dict(row[2]) if isinstance(row[2], dict) else row[2],
            )
            for row in rows
        ]

    def finish_conversation_stream(
        self,
        thread_id: str,
        *,
        status: str,
    ) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                UPDATE luxar_conversation_streams
                SET status = %s, updated_at = now()
                WHERE thread_id = %s
                """,
                (status, thread_id),
            )

    def start_run(
        self,
        *,
        thread_id: str,
        task_key: str,
        project_name: str,
        root_index: int,
        task_text: str,
        runtime_config: dict[str, object],
    ) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO luxar_workflow_runs
                    (thread_id, task_key, project_name, root_index, task_text,
                     status, runtime_config)
                VALUES (%s, %s, %s, %s, %s, 'running', %s::jsonb)
                ON CONFLICT (thread_id) DO NOTHING
                """,
                (
                    thread_id,
                    task_key,
                    project_name,
                    root_index,
                    task_text,
                    self._json(runtime_config),
                ),
            )

    def finish_run(
        self,
        thread_id: str,
        *,
        status: str,
        result: dict[str, object] | None,
    ) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                UPDATE luxar_workflow_runs
                SET status = %s, result = %s::jsonb, updated_at = now()
                WHERE thread_id = %s
                """,
                (status, self._json(result) if result is not None else None, thread_id),
            )

    def get_latest_completed_run(
        self,
        task_key: str,
    ) -> WorkflowRunRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT thread_id, task_key, task_text, status, result
                FROM luxar_workflow_runs
                WHERE task_key = %s AND status = 'completed' AND result IS NOT NULL
                ORDER BY updated_at DESC LIMIT 1
                """,
                (task_key,),
            ).fetchone()
        if row is None:
            return None
        return WorkflowRunRecord(
            thread_id=row[0],
            task_key=row[1],
            task_text=row[2],
            status=row[3],
            result=dict(row[4]),
        )

    def get_latest_run(self, task_key: str) -> WorkflowRunRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT thread_id, task_key, task_text, status, result,
                       runtime_config
                FROM luxar_workflow_runs
                WHERE task_key = %s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (task_key,),
            ).fetchone()
        if row is None:
            return None
        return WorkflowRunRecord(
            thread_id=row[0],
            task_key=row[1],
            task_text=row[2],
            status=row[3],
            result=dict(row[4]) if row[4] is not None else {},
            workflow_family=_optional_text(dict(row[5]).get("workflow_family")),
        )

    def get_runtime_observation_baseline(self) -> datetime:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT started_at FROM luxar_runtime_observation_baseline "
                "WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("运行时观察基线不存在")
        value = row[0]
        if not isinstance(value, datetime):
            raise RuntimeError("运行时观察基线格式无效")
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

    def list_runtime_observations(
        self,
        *,
        since: datetime,
    ) -> list[RuntimeObservationRecord]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT thread_id, status, runtime_config, created_at
                FROM luxar_workflow_runs
                WHERE created_at >= %s
                ORDER BY created_at, thread_id
                """,
                (since,),
            ).fetchall()
        observations: list[RuntimeObservationRecord] = []
        for row in rows:
            config = dict(row[2])
            created_at = row[3]
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            observations.append(
                RuntimeObservationRecord(
                    thread_id=row[0],
                    status=row[1],
                    workflow_family=_optional_text(
                        config.get("workflow_family")
                    ),
                    firmware_runtime=_optional_text(
                        config.get("firmware_runtime")
                    ),
                    agent_runtime=_optional_text(
                        config.get("agent_runtime")
                    ),
                    created_at=created_at.astimezone(timezone.utc),
                )
            )
        return observations

    def list_pending_runtime_approvals(
        self,
    ) -> list[PendingRuntimeApproval]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT thread_id, runtime_config
                FROM luxar_approval_requests
                WHERE status = 'pending'
                ORDER BY created_at, task_key
                """
            ).fetchall()
        results: list[PendingRuntimeApproval] = []
        for row in rows:
            config = dict(row[1])
            results.append(
                PendingRuntimeApproval(
                    thread_id=row[0],
                    workflow_family=_optional_text(
                        config.get("workflow_family")
                    ),
                    firmware_runtime=_optional_text(
                        config.get("firmware_runtime")
                    ),
                    agent_runtime=_optional_text(
                        config.get("agent_runtime")
                    ),
                )
            )
        return results

    def save_pending_approval(self, record: PendingApprovalRecord) -> None:
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO luxar_approval_requests
                        (task_key, project_name, root_index, thread_id, request,
                         runtime_config, status, decision, updated_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb,
                            'pending', NULL, now())
                    ON CONFLICT (task_key) DO UPDATE SET
                        project_name = EXCLUDED.project_name,
                        root_index = EXCLUDED.root_index,
                        thread_id = EXCLUDED.thread_id,
                        request = EXCLUDED.request,
                        runtime_config = EXCLUDED.runtime_config,
                        status = 'pending', decision = NULL,
                        created_at = now(), decided_at = NULL, updated_at = now()
                    """,
                    (
                        record.task_key,
                        record.project_name,
                        record.root_index,
                        record.thread_id,
                        self._json(record.request),
                        self._json(record.runtime_config),
                    ),
                )
                connection.execute(
                    """
                    UPDATE luxar_workflow_runs
                    SET status = 'pending_approval', updated_at = now()
                    WHERE thread_id = %s
                    """,
                    (record.thread_id,),
                )

    def get_pending_approval(
        self, task_key: str
    ) -> PendingApprovalRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT task_key, project_name, root_index, thread_id,
                       request, runtime_config, status, decision
                FROM luxar_approval_requests
                WHERE task_key = %s AND status = 'pending'
                """,
                (task_key,),
            ).fetchone()
        if row is None:
            return None
        return PendingApprovalRecord(
            task_key=row[0],
            project_name=row[1],
            root_index=row[2],
            thread_id=row[3],
            request=dict(row[4]),
            runtime_config=dict(row[5]),
            status=row[6],
            decision=row[7],
        )

    def decide_approval(self, task_key: str, approved: bool) -> bool:
        with self._pool.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE luxar_approval_requests
                SET status = 'decided', decision = %s,
                    decided_at = now(), updated_at = now()
                WHERE task_key = %s AND status = 'pending'
                """,
                (approved, task_key),
            )
            return cursor.rowcount == 1

    def complete_approval(self, task_key: str, *, failed: bool = False) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                UPDATE luxar_approval_requests
                SET status = %s, updated_at = now()
                WHERE task_key = %s
                """,
                ("failed" if failed else "completed", task_key),
            )

    def upsert_memory(
        self,
        *,
        project_key: str,
        memory_key: str,
        memory_type: str,
        value: dict[str, object],
        confidence: float = 1.0,
        source_thread_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        if not 0 <= confidence <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")
        with self._pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO luxar_project_memories
                    (project_key, memory_key, memory_type, value,
                     source_thread_id, confidence, expires_at)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (project_key, memory_key) DO UPDATE SET
                    memory_type = EXCLUDED.memory_type,
                    value = EXCLUDED.value,
                    source_thread_id = EXCLUDED.source_thread_id,
                    confidence = EXCLUDED.confidence,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = now()
                """,
                (
                    project_key,
                    memory_key,
                    memory_type,
                    self._json(value),
                    source_thread_id,
                    confidence,
                    expires_at,
                ),
            )

    def find_memories(
        self,
        project_key: str,
        *,
        memory_type: str | None = None,
        limit: int = 20,
    ) -> list[ProjectMemory]:
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        query = """
            SELECT project_key, memory_key, memory_type, value,
                   confidence, source_thread_id
            FROM luxar_project_memories
            WHERE project_key = %s
              AND (expires_at IS NULL OR expires_at > now())
        """
        params: list[object] = [project_key]
        if memory_type is not None:
            query += " AND memory_type = %s"
            params.append(memory_type)
        query += " ORDER BY confidence DESC, updated_at DESC LIMIT %s"
        params.append(limit)
        with self._pool.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            ProjectMemory(
                project_key=row[0],
                memory_key=row[1],
                memory_type=row[2],
                value=dict(row[3]),
                confidence=row[4],
                source_thread_id=row[5],
            )
            for row in rows
        ]

    def save_agent_project(
        self,
        *,
        project_key: str,
        objective: dict[str, object],
        change_set: dict[str, object],
        revision: int,
        capabilities: list[dict[str, object]],
        snapshot: dict[str, object] | None = None,
    ) -> None:
        if not project_key.strip() or revision < 1:
            raise ValueError("agent project key 和 revision 无效")
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO luxar_agent_objectives
                        (project_key, objective_id, revision, objective, change_set,
                         snapshot)
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                    ON CONFLICT (project_key) DO UPDATE SET
                        objective_id = EXCLUDED.objective_id,
                        revision = EXCLUDED.revision,
                        objective = EXCLUDED.objective,
                        change_set = EXCLUDED.change_set,
                        snapshot = EXCLUDED.snapshot,
                        updated_at = now()
                    """,
                    (
                        project_key,
                        str(objective.get("objective_id", "")),
                        revision,
                        self._json(objective),
                        self._json(change_set),
                        self._json(snapshot or {}),
                    ),
                )
                connection.execute(
                    "DELETE FROM luxar_agent_capabilities WHERE project_key = %s",
                    (project_key,),
                )
                if capabilities:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """
                            INSERT INTO luxar_agent_capabilities
                                (project_key, capability_id, revision, capability)
                            VALUES (%s, %s, %s, %s::jsonb)
                            """,
                            [
                                (
                                    project_key,
                                    str(capability.get("capability_id", "")),
                                    revision,
                                    self._json(capability),
                                )
                                for capability in capabilities
                            ],
                        )

    def get_agent_project(self, project_key: str) -> AgentProjectRecord | None:
        with self._pool.connection() as connection:
            objective_row = connection.execute(
                """
                SELECT objective, change_set, revision, snapshot
                FROM luxar_agent_objectives WHERE project_key = %s
                """,
                (project_key,),
            ).fetchone()
            if objective_row is None:
                return None
            capability_rows = connection.execute(
                """
                SELECT capability FROM luxar_agent_capabilities
                WHERE project_key = %s ORDER BY capability_id
                """,
                (project_key,),
            ).fetchall()
        return AgentProjectRecord(
            project_key=project_key,
            objective=dict(objective_row[0]),
            change_set=dict(objective_row[1]),
            revision=int(objective_row[2]),
            capabilities=[dict(row[0]) for row in capability_rows],
            snapshot=dict(objective_row[3]),
        )

    def save_workbench_snapshot(
        self,
        *,
        project_key: str,
        workflow_family: str,
        thread_id: str,
        snapshot: dict[str, object],
    ) -> None:
        if (
            not project_key.strip()
            or not workflow_family.strip()
            or not thread_id.strip()
        ):
            raise ValueError("workbench snapshot 标识不能为空")
        with self._pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO luxar_workbench_snapshots
                    (project_key, workflow_family, thread_id, snapshot)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (project_key) DO UPDATE SET
                    workflow_family = EXCLUDED.workflow_family,
                    thread_id = EXCLUDED.thread_id,
                    snapshot = EXCLUDED.snapshot,
                    updated_at = now()
                """,
                (
                    project_key,
                    workflow_family,
                    thread_id,
                    self._json(snapshot),
                ),
            )

    def get_workbench_snapshot(
        self,
        project_key: str,
    ) -> WorkbenchSnapshotRecord | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT project_key, workflow_family, thread_id, snapshot, updated_at
                FROM luxar_workbench_snapshots WHERE project_key = %s
                """,
                (project_key,),
            ).fetchone()
        if row is None:
            return None
        updated_at = row[4]
        if not isinstance(updated_at, datetime):
            raise RuntimeError("workbench snapshot 时间格式无效")
        return WorkbenchSnapshotRecord(
            project_key=row[0],
            workflow_family=row[1],
            thread_id=row[2],
            snapshot=dict(row[3]),
            updated_at=(
                updated_at.replace(tzinfo=timezone.utc)
                if updated_at.tzinfo is None
                else updated_at.astimezone(timezone.utc)
            ),
        )

    def append_agent_interaction(
        self,
        *,
        interaction_id: str,
        project_key: str,
        objective_id: str | None,
        kind: str,
        payload: dict[str, object],
    ) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO luxar_agent_interactions
                    (interaction_id, project_key, objective_id, kind, payload)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (interaction_id) DO NOTHING
                """,
                (
                    interaction_id,
                    project_key,
                    objective_id,
                    kind,
                    self._json(payload),
                ),
            )

    def get_agent_interactions(
        self,
        project_key: str,
        *,
        limit: int = 100,
    ) -> list[AgentInteractionRecord]:
        if not 1 <= limit <= 500:
            raise ValueError("limit 必须在 1 到 500 之间")
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT interaction_id, project_key, objective_id, kind, payload
                FROM luxar_agent_interactions
                WHERE project_key = %s ORDER BY created_at, interaction_id
                LIMIT %s
                """,
                (project_key, limit),
            ).fetchall()
        return [
            AgentInteractionRecord(
                interaction_id=row[0],
                project_key=row[1],
                objective_id=row[2],
                kind=row[3],
                payload=dict(row[4]),
            )
            for row in rows
        ]

    def replace_document(
        self,
        *,
        document_id: str,
        project_key: str,
        source_uri: str,
        title: str,
        content_hash: str,
        metadata: dict[str, object],
        chunks: list[KnowledgeChunk | tuple[str, int, list[float]]],
    ) -> None:
        normalized_chunks = [
            chunk
            if isinstance(chunk, KnowledgeChunk)
            else KnowledgeChunk(
                content=chunk[0],
                token_count=chunk[1],
                embedding=chunk[2],
            )
            for chunk in chunks
        ]
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    """
                    INSERT INTO luxar_knowledge_documents
                        (id, project_key, source_uri, title, content_hash, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        content_hash = EXCLUDED.content_hash,
                        metadata = EXCLUDED.metadata,
                        updated_at = now()
                    """,
                    (
                        document_id,
                        project_key,
                        source_uri,
                        title,
                        content_hash,
                        self._json(metadata),
                    ),
                )
                connection.execute(
                    "DELETE FROM luxar_knowledge_chunks WHERE document_id = %s",
                    (document_id,),
                )
                for ordinal, chunk in enumerate(normalized_chunks):
                    connection.execute(
                        """
                        INSERT INTO luxar_knowledge_chunks
                            (document_id, ordinal, content, token_count, embedding,
                             metadata)
                        VALUES (%s, %s, %s, %s, %s::vector, %s::jsonb)
                        """,
                        (
                            document_id,
                            ordinal,
                            chunk.content,
                            chunk.token_count,
                            "[" + ",".join(
                                str(value) for value in chunk.embedding
                            ) + "]",
                            self._json(chunk.metadata),
                        ),
                    )

    def search_knowledge(
        self,
        *,
        project_key: str,
        query_text: str,
        query_embedding: list[float],
        limit: int = 6,
    ) -> list[KnowledgeMatch]:
        vector = "[" + ",".join(str(value) for value in query_embedding) + "]"
        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT d.id::text, d.title, d.source_uri, c.ordinal, c.content,
                       c.metadata,
                       (0.7 * (1 - (c.embedding <=> %s::vector)) +
                        0.3 * ts_rank_cd(c.search_vector,
                              plainto_tsquery('simple', %s))) AS score
                FROM luxar_knowledge_chunks c
                JOIN luxar_knowledge_documents d ON d.id = c.document_id
                WHERE d.project_key = %s
                ORDER BY score DESC
                LIMIT %s
                """,
                (vector, query_text, project_key, limit),
            ).fetchall()
        return [
            KnowledgeMatch(
                document_id=row[0],
                title=row[1],
                source_uri=row[2],
                ordinal=row[3],
                content=row[4],
                score=float(row[6]),
                knowledge_id=_optional_text(dict(row[5]).get("knowledge_id")),
                subject=_optional_text(dict(row[5]).get("subject")),
                category=_optional_text(dict(row[5]).get("category")),
                source_pages=tuple(
                    int(page)
                    for page in dict(row[5]).get("source_pages", [])
                    if isinstance(page, int) and page > 0
                ),
                source_section=_optional_text(
                    dict(row[5]).get("source_section")
                ),
                applicable_conditions=tuple(
                    str(value)
                    for value in dict(row[5]).get("applicable_conditions", [])
                    if isinstance(value, str)
                ),
                limitations=tuple(
                    str(value)
                    for value in dict(row[5]).get("limitations", [])
                    if isinstance(value, str)
                ),
                metadata=dict(row[5]),
            )
            for row in rows
        ]

    def count_knowledge_documents(self, project_key: str) -> int:
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM luxar_knowledge_documents
                WHERE project_key = %s
                """,
                (project_key,),
            ).fetchone()
        return int(row[0]) if row else 0
