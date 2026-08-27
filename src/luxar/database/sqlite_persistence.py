"""Durable application records stored in an embedded SQLite database."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from luxar.database.persistence import (
    AgentSessionRecord,
    AgentTurnRecord,
    AgentInteractionRecord,
    AgentProjectRecord,
    PendingApprovalRecord,
    PendingRuntimeApproval,
    ProjectMemory,
    RuntimeObservationRecord,
    WorkbenchSnapshotRecord,
    WorkflowRunRecord,
    ConversationStreamEventRecord,
    ConversationStreamRecord,
)
from luxar.domain.continuous_agent.failures import ContinuousAgentFailure
from luxar.domain.continuous_agent.tools import (
    ToolExecutionLedgerStatus,
    ToolExecutionRecord,
)


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS luxar_workflow_runs (
    thread_id TEXT PRIMARY KEY,
    task_key TEXT NOT NULL,
    project_name TEXT NOT NULL,
    root_index INTEGER NOT NULL CHECK (root_index >= 0),
    task_text TEXT NOT NULL,
    status TEXT NOT NULL,
    runtime_config TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS luxar_workflow_runs_task_idx
    ON luxar_workflow_runs (task_key, created_at DESC);

CREATE TABLE IF NOT EXISTS luxar_runtime_observation_baseline (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO luxar_runtime_observation_baseline (singleton) VALUES (1);

CREATE TABLE IF NOT EXISTS luxar_conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    thread_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS luxar_messages_task_idx
    ON luxar_conversation_messages (task_key, id);

CREATE TABLE IF NOT EXISTS luxar_conversation_streams (
    thread_id TEXT PRIMARY KEY,
    task_key TEXT NOT NULL,
    user_message TEXT NOT NULL,
    assistant_content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN (
            'running', 'pending_approval', 'completed', 'failed', 'interrupted'
        )),
    last_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    last_event TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS luxar_conversation_streams_task_idx
    ON luxar_conversation_streams (task_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS luxar_conversation_stream_events (
    thread_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    event TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (thread_id, sequence),
    FOREIGN KEY (thread_id) REFERENCES luxar_conversation_streams(thread_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS luxar_agent_sessions (
    session_id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    active_objective_id TEXT,
    context_summary TEXT NOT NULL DEFAULT '',
    compaction_cursor INTEGER NOT NULL DEFAULT 0
        CHECK (compaction_cursor >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS luxar_agent_sessions_project_idx
    ON luxar_agent_sessions (project_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS luxar_agent_turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    client_turn_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN (
            'running', 'waiting_input', 'waiting_approval',
            'completed', 'failed', 'cancelled'
        )),
    user_message TEXT NOT NULL,
    assistant_message TEXT NOT NULL DEFAULT '',
    failure TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (session_id, client_turn_id),
    FOREIGN KEY (session_id) REFERENCES luxar_agent_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS luxar_agent_turns_session_idx
    ON luxar_agent_turns (session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS luxar_tool_executions (
    idempotency_key TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN (
            'running', 'succeeded', 'failed', 'rejected', 'indeterminate'
        )),
    result TEXT,
    failure TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES luxar_agent_sessions(session_id),
    FOREIGN KEY (turn_id) REFERENCES luxar_agent_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS luxar_tool_executions_turn_idx
    ON luxar_tool_executions (turn_id, created_at);

CREATE TABLE IF NOT EXISTS luxar_approval_requests (
    task_key TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    root_index INTEGER NOT NULL CHECK (root_index >= 0),
    thread_id TEXT NOT NULL,
    request TEXT NOT NULL,
    runtime_config TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'decided', 'completed', 'failed')),
    decision INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS luxar_approvals_status_idx
    ON luxar_approval_requests (status, created_at);

CREATE TABLE IF NOT EXISTS luxar_project_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    value TEXT NOT NULL,
    source_thread_id TEXT,
    confidence REAL NOT NULL DEFAULT 1.0
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    expires_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_key, memory_key)
);

CREATE INDEX IF NOT EXISTS luxar_memories_lookup_idx
    ON luxar_project_memories (project_key, memory_type, updated_at DESC);

CREATE TABLE IF NOT EXISTS luxar_agent_objectives (
    project_key TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    objective TEXT NOT NULL,
    change_set TEXT NOT NULL,
    snapshot TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS luxar_agent_capabilities (
    project_key TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    capability TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_key, capability_id),
    FOREIGN KEY (project_key) REFERENCES luxar_agent_objectives(project_key)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS luxar_agent_capabilities_revision_idx
    ON luxar_agent_capabilities (project_key, revision, capability_id);

CREATE TABLE IF NOT EXISTS luxar_agent_interactions (
    interaction_id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL,
    objective_id TEXT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_key) REFERENCES luxar_agent_objectives(project_key)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS luxar_agent_interactions_project_idx
    ON luxar_agent_interactions (project_key, created_at, interaction_id);

CREATE TABLE IF NOT EXISTS luxar_workbench_snapshots (
    project_key TEXT PRIMARY KEY,
    workflow_family TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS luxar_data_migrations (
    migration_key TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class SQLitePersistence:
    """Thread-safe-by-connection SQLite implementation for local LUXAR data."""

    durable = True

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(_SCHEMA)
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(luxar_agent_objectives)"
                ).fetchall()
            }
            if "snapshot" not in columns:
                connection.execute(
                    "ALTER TABLE luxar_agent_objectives "
                    "ADD COLUMN snapshot TEXT NOT NULL DEFAULT '{}'"
                )

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _parse_timestamp(value: object) -> datetime:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def health(self) -> bool:
        try:
            with self._connection() as connection:
                row = connection.execute("SELECT 1").fetchone()
            return row is not None and row[0] == 1
        except sqlite3.Error:
            return False

    @classmethod
    def _sqlite_agent_session(cls, row: sqlite3.Row) -> AgentSessionRecord:
        return AgentSessionRecord(
            session_id=row["session_id"],
            project_key=row["project_key"],
            status=row["status"],
            active_objective_id=row["active_objective_id"],
            context_summary=row["context_summary"],
            compaction_cursor=int(row["compaction_cursor"]),
            created_at=cls._parse_timestamp(row["created_at"]),
            updated_at=cls._parse_timestamp(row["updated_at"]),
        )

    @classmethod
    def _sqlite_agent_turn(cls, row: sqlite3.Row) -> AgentTurnRecord:
        failure = json.loads(row["failure"]) if row["failure"] else None
        return AgentTurnRecord(
            turn_id=row["turn_id"],
            session_id=row["session_id"],
            client_turn_id=row["client_turn_id"],
            status=row["status"],
            user_message=row["user_message"],
            assistant_message=row["assistant_message"],
            failure=failure,
            created_at=cls._parse_timestamp(row["created_at"]),
            updated_at=cls._parse_timestamp(row["updated_at"]),
        )

    def create_agent_session(
        self,
        *,
        session_id: str,
        project_key: str,
    ) -> AgentSessionRecord:
        if not session_id.strip() or not project_key.strip():
            raise ValueError("Agent Session 标识不能为空")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO luxar_agent_sessions
                    (session_id, project_key)
                VALUES (?, ?)
                """,
                (session_id, project_key),
            )
            row = connection.execute(
                """
                SELECT session_id, project_key, status, active_objective_id,
                       context_summary, compaction_cursor, created_at, updated_at
                FROM luxar_agent_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Agent Session 创建失败")
        record = self._sqlite_agent_session(row)
        if record.project_key != project_key:
            raise ValueError("Agent Session 已属于其他项目")
        return record

    def get_agent_session(
        self,
        session_id: str,
    ) -> AgentSessionRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT session_id, project_key, status, active_objective_id,
                       context_summary, compaction_cursor, created_at, updated_at
                FROM luxar_agent_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return self._sqlite_agent_session(row) if row is not None else None

    def get_active_agent_session(
        self,
        project_key: str,
    ) -> AgentSessionRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT session_id, project_key, status, active_objective_id,
                       context_summary, compaction_cursor, created_at, updated_at
                FROM luxar_agent_sessions
                WHERE project_key = ? AND status = 'active'
                ORDER BY updated_at DESC, rowid DESC LIMIT 1
                """,
                (project_key,),
            ).fetchone()
        return self._sqlite_agent_session(row) if row is not None else None

    def archive_agent_session(self, session_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE luxar_agent_sessions
                SET status = 'archived', updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                """,
                (session_id,),
            )
        return cursor.rowcount > 0

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
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE luxar_agent_sessions
                SET active_objective_id = ?, context_summary = ?,
                    compaction_cursor = ?, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                """,
                (
                    active_objective_id,
                    context_summary,
                    compaction_cursor,
                    session_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError("Agent Session 不存在")
            row = connection.execute(
                """
                SELECT session_id, project_key, status, active_objective_id,
                       context_summary, compaction_cursor, created_at, updated_at
                FROM luxar_agent_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        assert row is not None
        return self._sqlite_agent_session(row)

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
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT turn_id, session_id, client_turn_id, status,
                       user_message, assistant_message, failure,
                       created_at, updated_at
                FROM luxar_agent_turns
                WHERE session_id = ? AND client_turn_id = ?
                """,
                (session_id, client_turn_id),
            ).fetchone()
            if existing is not None:
                return self._sqlite_agent_turn(existing)
            session = connection.execute(
                """
                SELECT status FROM luxar_agent_sessions WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if session is None or session["status"] != "active":
                raise ValueError("Agent Session 不存在或已归档")
            try:
                connection.execute(
                    """
                    INSERT INTO luxar_agent_turns
                        (turn_id, session_id, client_turn_id, user_message)
                    VALUES (?, ?, ?, ?)
                    """,
                    (turn_id, session_id, client_turn_id, user_message),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("Agent Turn 标识冲突") from error
            connection.execute(
                """
                UPDATE luxar_agent_sessions SET updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                """,
                (session_id,),
            )
            row = connection.execute(
                """
                SELECT turn_id, session_id, client_turn_id, status,
                       user_message, assistant_message, failure,
                       created_at, updated_at
                FROM luxar_agent_turns WHERE turn_id = ?
                """,
                (turn_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Agent Turn 创建失败")
        return self._sqlite_agent_turn(row)

    def get_agent_turn(self, turn_id: str) -> AgentTurnRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT turn_id, session_id, client_turn_id, status,
                       user_message, assistant_message, failure,
                       created_at, updated_at
                FROM luxar_agent_turns WHERE turn_id = ?
                """,
                (turn_id,),
            ).fetchone()
        return self._sqlite_agent_turn(row) if row is not None else None

    def get_agent_turn_by_client_id(
        self,
        *,
        session_id: str,
        client_turn_id: str,
    ) -> AgentTurnRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT turn_id, session_id, client_turn_id, status,
                       user_message, assistant_message, failure,
                       created_at, updated_at
                FROM luxar_agent_turns
                WHERE session_id = ? AND client_turn_id = ?
                """,
                (session_id, client_turn_id),
            ).fetchone()
        return self._sqlite_agent_turn(row) if row is not None else None

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
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE luxar_agent_turns
                SET status = ?, assistant_message = ?, failure = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE turn_id = ?
                """,
                (
                    status,
                    assistant_message,
                    self._json(failure) if failure is not None else None,
                    turn_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError("Agent Turn 不存在")
            connection.execute(
                """
                UPDATE luxar_agent_sessions SET updated_at = CURRENT_TIMESTAMP
                WHERE session_id = (
                    SELECT session_id FROM luxar_agent_turns WHERE turn_id = ?
                )
                """,
                (turn_id,),
            )

    @staticmethod
    def _sqlite_tool_execution(row: sqlite3.Row) -> ToolExecutionRecord:
        raw_failure = json.loads(row["failure"]) if row["failure"] else None
        return ToolExecutionRecord(
            idempotency_key=row["idempotency_key"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            call_id=row["call_id"],
            tool_name=row["tool_name"],
            arguments_fingerprint=row["arguments_fingerprint"],
            status=row["status"],
            result=json.loads(row["result"]) if row["result"] else None,
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
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO luxar_tool_executions
                    (idempotency_key, session_id, turn_id, call_id,
                     tool_name, arguments_fingerprint, status)
                VALUES (?, ?, ?, ?, ?, ?, 'running')
                """,
                (
                    idempotency_key,
                    session_id,
                    turn_id,
                    call_id,
                    tool_name,
                    arguments_fingerprint,
                ),
            )
            created = cursor.rowcount > 0
            row = connection.execute(
                """
                SELECT idempotency_key, session_id, turn_id, call_id,
                       tool_name, arguments_fingerprint, status, result, failure
                FROM luxar_tool_executions WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Tool execution reservation failed")
        record = self._sqlite_tool_execution(row)
        if (
            record.session_id != session_id
            or record.turn_id != turn_id
            or record.call_id != call_id
            or record.tool_name != tool_name
            or record.arguments_fingerprint != arguments_fingerprint
        ):
            raise ValueError("Tool execution idempotency conflict")
        return record, created

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
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE luxar_tool_executions
                SET status = ?, result = ?, failure = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE idempotency_key = ?
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
            )
            if cursor.rowcount == 0:
                raise KeyError("Tool execution 不存在")
            row = connection.execute(
                """
                SELECT idempotency_key, session_id, turn_id, call_id,
                       tool_name, arguments_fingerprint, status, result, failure
                FROM luxar_tool_executions WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Tool execution completion failed")
        return self._sqlite_tool_execution(row)

    def get_messages(self, task_key: str) -> list[dict[str, str]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT role, content FROM luxar_conversation_messages
                WHERE task_key = ? ORDER BY id
                """,
                (task_key,),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def append_exchange(
        self,
        task_key: str,
        *,
        thread_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        with self._connection() as connection:
            connection.executemany(
                """
                INSERT INTO luxar_conversation_messages
                    (task_key, role, content, thread_id)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (task_key, "user", user_message, thread_id),
                    (task_key, "assistant", assistant_message, thread_id),
                ],
            )

    def import_messages_once(
        self,
        migration_key: str,
        task_key: str,
        messages: list[dict[str, str]],
        *,
        thread_id: str,
    ) -> int:
        """Atomically import an ordered history exactly once."""

        if not migration_key.strip():
            raise ValueError("migration_key 不能为空")
        normalized: list[tuple[str, str, str, str]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"} or not content:
                raise ValueError("迁移消息格式无效")
            normalized.append((task_key, role, content, thread_id))

        with self._connection() as connection:
            applied = connection.execute(
                "SELECT 1 FROM luxar_data_migrations WHERE migration_key = ?",
                (migration_key,),
            ).fetchone()
            if applied is not None:
                return 0
            connection.executemany(
                """
                INSERT INTO luxar_conversation_messages
                    (task_key, role, content, thread_id)
                VALUES (?, ?, ?, ?)
                """,
                normalized,
            )
            connection.execute(
                "INSERT INTO luxar_data_migrations (migration_key) VALUES (?)",
                (migration_key,),
            )
        return len(normalized)

    def reset_conversation(self, task_key: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM luxar_conversation_messages WHERE task_key = ?",
                (task_key,),
            )
            connection.execute(
                "DELETE FROM luxar_conversation_streams WHERE task_key = ?",
                (task_key,),
            )

    def start_conversation_stream(
        self,
        *,
        thread_id: str,
        task_key: str,
        user_message: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                DELETE FROM luxar_conversation_streams
                WHERE task_key = ?
                  AND status NOT IN ('running', 'pending_approval')
                """,
                (task_key,),
            )
            connection.execute(
                """
                INSERT INTO luxar_conversation_streams
                    (thread_id, task_key, user_message, assistant_content,
                     status, last_sequence, last_event, updated_at)
                VALUES (?, ?, ?, '', 'running', 0, NULL, CURRENT_TIMESTAMP)
                ON CONFLICT(thread_id) DO UPDATE SET
                    task_key = excluded.task_key,
                    user_message = excluded.user_message,
                    assistant_content = '',
                    status = 'running',
                    last_sequence = 0,
                    last_event = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (thread_id, task_key, user_message),
            )
            connection.execute(
                "DELETE FROM luxar_conversation_stream_events WHERE thread_id = ?",
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
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT last_sequence, assistant_content
                FROM luxar_conversation_streams WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()
            if row is None:
                raise KeyError("conversation stream does not exist")
            sequence = int(row["last_sequence"]) + 1
            content = str(row["assistant_content"])
            if event == "token":
                content += token
            elif event == "reset_output":
                content = ""
            connection.execute(
                """
                UPDATE luxar_conversation_streams
                SET assistant_content = ?, last_sequence = ?, last_event = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE thread_id = ?
                """,
                (content, sequence, event, thread_id),
            )
            connection.execute(
                """
                INSERT INTO luxar_conversation_stream_events
                    (thread_id, sequence, event, data)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, sequence, event, self._json(data)),
            )
        return sequence

    @classmethod
    def _sqlite_stream_record(cls, row: sqlite3.Row) -> ConversationStreamRecord:
        return ConversationStreamRecord(
            thread_id=row["thread_id"],
            task_key=row["task_key"],
            user_message=row["user_message"],
            assistant_content=row["assistant_content"],
            status=row["status"],
            last_sequence=int(row["last_sequence"]),
            last_event=row["last_event"],
            updated_at=cls._parse_timestamp(row["updated_at"]),
        )

    def get_active_conversation_stream(
        self,
        task_key: str,
    ) -> ConversationStreamRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT thread_id, task_key, user_message, assistant_content,
                       status, last_sequence, last_event, updated_at
                FROM luxar_conversation_streams
                WHERE task_key = ?
                  AND status IN ('running', 'pending_approval')
                ORDER BY rowid DESC LIMIT 1
                """,
                (task_key,),
            ).fetchone()
        return self._sqlite_stream_record(row) if row is not None else None

    def get_conversation_stream(
        self,
        thread_id: str,
    ) -> ConversationStreamRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT thread_id, task_key, user_message, assistant_content,
                       status, last_sequence, last_event, updated_at
                FROM luxar_conversation_streams WHERE thread_id = ?
                """,
                (thread_id,),
            ).fetchone()
        return self._sqlite_stream_record(row) if row is not None else None

    def list_conversation_stream_events(
        self,
        thread_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[ConversationStreamEventRecord]:
        if after_sequence < 0 or not 1 <= limit <= 2000:
            raise ValueError("conversation stream event range is invalid")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT sequence, event, data
                FROM luxar_conversation_stream_events
                WHERE thread_id = ? AND sequence > ?
                ORDER BY sequence LIMIT ?
                """,
                (thread_id, after_sequence, limit),
            ).fetchall()
        return [
            ConversationStreamEventRecord(
                thread_id=thread_id,
                sequence=int(row["sequence"]),
                event=row["event"],
                data=json.loads(row["data"]),
            )
            for row in rows
        ]

    def finish_conversation_stream(
        self,
        thread_id: str,
        *,
        status: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE luxar_conversation_streams
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE thread_id = ?
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
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO luxar_workflow_runs
                    (thread_id, task_key, project_name, root_index, task_text,
                     status, runtime_config)
                VALUES (?, ?, ?, ?, ?, 'running', ?)
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
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE luxar_workflow_runs
                SET status = ?, result = ?, updated_at = CURRENT_TIMESTAMP
                WHERE thread_id = ?
                """,
                (status, self._json(result) if result is not None else None, thread_id),
            )

    def get_latest_completed_run(
        self,
        task_key: str,
    ) -> WorkflowRunRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT thread_id, task_key, task_text, status, result
                FROM luxar_workflow_runs
                WHERE task_key = ? AND status = 'completed' AND result IS NOT NULL
                ORDER BY rowid DESC LIMIT 1
                """,
                (task_key,),
            ).fetchone()
        if row is None:
            return None
        return WorkflowRunRecord(
            thread_id=row["thread_id"],
            task_key=row["task_key"],
            task_text=row["task_text"],
            status=row["status"],
            result=json.loads(row["result"]),
        )

    def get_latest_run(self, task_key: str) -> WorkflowRunRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT thread_id, task_key, task_text, status, result,
                       runtime_config
                FROM luxar_workflow_runs
                WHERE task_key = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (task_key,),
            ).fetchone()
        if row is None:
            return None
        return WorkflowRunRecord(
            thread_id=row["thread_id"],
            task_key=row["task_key"],
            task_text=row["task_text"],
            status=row["status"],
            result=(json.loads(row["result"]) if row["result"] else {}),
            workflow_family=(
                json.loads(row["runtime_config"]).get("workflow_family")
            ),
        )

    def get_runtime_observation_baseline(self) -> datetime:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT started_at FROM luxar_runtime_observation_baseline "
                "WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("运行时观察基线不存在")
        return self._parse_timestamp(row["started_at"])

    def list_runtime_observations(
        self,
        *,
        since: datetime,
    ) -> list[RuntimeObservationRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT thread_id, status, runtime_config, created_at
                FROM luxar_workflow_runs
                WHERE datetime(created_at) >= datetime(?)
                ORDER BY created_at, thread_id
                """,
                (self._timestamp(since),),
            ).fetchall()
        observations: list[RuntimeObservationRecord] = []
        for row in rows:
            config = json.loads(row["runtime_config"])
            observations.append(
                RuntimeObservationRecord(
                    thread_id=row["thread_id"],
                    status=row["status"],
                    workflow_family=config.get("workflow_family"),
                    firmware_runtime=config.get("firmware_runtime"),
                    agent_runtime=config.get("agent_runtime"),
                    created_at=self._parse_timestamp(row["created_at"]),
                )
            )
        return observations

    def list_pending_runtime_approvals(
        self,
    ) -> list[PendingRuntimeApproval]:
        with self._connection() as connection:
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
            config = json.loads(row["runtime_config"])
            results.append(
                PendingRuntimeApproval(
                    thread_id=row["thread_id"],
                    workflow_family=config.get("workflow_family"),
                    firmware_runtime=config.get("firmware_runtime"),
                    agent_runtime=config.get("agent_runtime"),
                )
            )
        return results

    def save_pending_approval(self, record: PendingApprovalRecord) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO luxar_approval_requests
                    (task_key, project_name, root_index, thread_id, request,
                     runtime_config, status, decision, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, CURRENT_TIMESTAMP)
                ON CONFLICT(task_key) DO UPDATE SET
                    project_name = excluded.project_name,
                    root_index = excluded.root_index,
                    thread_id = excluded.thread_id,
                    request = excluded.request,
                    runtime_config = excluded.runtime_config,
                    status = 'pending', decision = NULL,
                    created_at = CURRENT_TIMESTAMP,
                    decided_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
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
                SET status = 'pending_approval', updated_at = CURRENT_TIMESTAMP
                WHERE thread_id = ?
                """,
                (record.thread_id,),
            )

    def get_pending_approval(self, task_key: str) -> PendingApprovalRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT task_key, project_name, root_index, thread_id,
                       request, runtime_config, status, decision
                FROM luxar_approval_requests
                WHERE task_key = ? AND status = 'pending'
                """,
                (task_key,),
            ).fetchone()
        if row is None:
            return None
        return PendingApprovalRecord(
            task_key=row["task_key"],
            project_name=row["project_name"],
            root_index=row["root_index"],
            thread_id=row["thread_id"],
            request=json.loads(row["request"]),
            runtime_config=json.loads(row["runtime_config"]),
            status=row["status"],
            decision=None if row["decision"] is None else bool(row["decision"]),
        )

    def decide_approval(self, task_key: str, approved: bool) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE luxar_approval_requests
                SET status = 'decided', decision = ?,
                    decided_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE task_key = ? AND status = 'pending'
                """,
                (int(approved), task_key),
            )
            return cursor.rowcount == 1

    def complete_approval(self, task_key: str, *, failed: bool = False) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE luxar_approval_requests
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_key = ?
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
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO luxar_project_memories
                    (project_key, memory_key, memory_type, value,
                     source_thread_id, confidence, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_key, memory_key) DO UPDATE SET
                    memory_type = excluded.memory_type,
                    value = excluded.value,
                    source_thread_id = excluded.source_thread_id,
                    confidence = excluded.confidence,
                    expires_at = excluded.expires_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project_key,
                    memory_key,
                    memory_type,
                    self._json(value),
                    source_thread_id,
                    confidence,
                    self._timestamp(expires_at),
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
            WHERE project_key = ?
              AND (expires_at IS NULL OR expires_at > ?)
        """
        params: list[object] = [
            project_key,
            datetime.now(timezone.utc).isoformat(),
        ]
        if memory_type is not None:
            query += " AND memory_type = ?"
            params.append(memory_type)
        query += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            ProjectMemory(
                project_key=row["project_key"],
                memory_key=row["memory_key"],
                memory_type=row["memory_type"],
                value=json.loads(row["value"]),
                confidence=row["confidence"],
                source_thread_id=row["source_thread_id"],
            )
            for row in rows
        ]

    # Knowledge storage belongs to LanceDB. Keeping these methods explicit
    # prevents accidental use of the application database as a vector index.
    def replace_document(self, **_: object) -> None:
        raise RuntimeError("外部知识库由 LanceDBKnowledgeIndex 提供")

    def search_knowledge(self, **_: object) -> list[object]:
        return []

    def count_knowledge_documents(self, project_key: str) -> int:
        del project_key
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
        objective_id = str(objective.get("objective_id", ""))
        if not objective_id:
            raise ValueError("objective_id 不能为空")
        with self._connection() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO luxar_agent_objectives
                        (project_key, objective_id, revision, objective, change_set,
                         snapshot, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(project_key) DO UPDATE SET
                        objective_id = excluded.objective_id,
                        revision = excluded.revision,
                        objective = excluded.objective,
                        change_set = excluded.change_set,
                        snapshot = excluded.snapshot,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        project_key,
                        objective_id,
                        revision,
                        self._json(objective),
                        self._json(change_set),
                        self._json(snapshot or {}),
                    ),
                )
                connection.execute(
                    "DELETE FROM luxar_agent_capabilities WHERE project_key = ?",
                    (project_key,),
                )
                connection.executemany(
                    """
                    INSERT INTO luxar_agent_capabilities
                        (project_key, capability_id, revision, capability)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            project_key,
                            str(capability.get("capability_id", "")),
                            revision,
                            self._json(capability),
                        )
                        for capability in capabilities
                        if capability.get("capability_id")
                    ],
                )

    def get_agent_project(self, project_key: str) -> AgentProjectRecord | None:
        with self._connection() as connection:
            objective_row = connection.execute(
                """
                SELECT objective, change_set, revision, snapshot
                FROM luxar_agent_objectives WHERE project_key = ?
                """,
                (project_key,),
            ).fetchone()
            if objective_row is None:
                return None
            capability_rows = connection.execute(
                """
                SELECT capability FROM luxar_agent_capabilities
                WHERE project_key = ? ORDER BY capability_id
                """,
                (project_key,),
            ).fetchall()
        return AgentProjectRecord(
            project_key=project_key,
            objective=json.loads(objective_row["objective"]),
            change_set=json.loads(objective_row["change_set"]),
            revision=int(objective_row["revision"]),
            capabilities=[json.loads(row["capability"]) for row in capability_rows],
            snapshot=json.loads(objective_row["snapshot"]),
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
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO luxar_workbench_snapshots
                    (project_key, workflow_family, thread_id, snapshot, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(project_key) DO UPDATE SET
                    workflow_family = excluded.workflow_family,
                    thread_id = excluded.thread_id,
                    snapshot = excluded.snapshot,
                    updated_at = CURRENT_TIMESTAMP
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
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT project_key, workflow_family, thread_id, snapshot, updated_at
                FROM luxar_workbench_snapshots WHERE project_key = ?
                """,
                (project_key,),
            ).fetchone()
        if row is None:
            return None
        return WorkbenchSnapshotRecord(
            project_key=row["project_key"],
            workflow_family=row["workflow_family"],
            thread_id=row["thread_id"],
            snapshot=json.loads(row["snapshot"]),
            updated_at=self._parse_timestamp(row["updated_at"]),
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
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO luxar_agent_interactions
                    (interaction_id, project_key, objective_id, kind, payload)
                VALUES (?, ?, ?, ?, ?)
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
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT interaction_id, project_key, objective_id, kind, payload
                FROM luxar_agent_interactions
                WHERE project_key = ? ORDER BY created_at, interaction_id
                LIMIT ?
                """,
                (project_key, limit),
            ).fetchall()
        return [
            AgentInteractionRecord(
                interaction_id=row["interaction_id"],
                project_key=row["project_key"],
                objective_id=row["objective_id"],
                kind=row["kind"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]
