"""Durable application records stored in an embedded SQLite database."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from luxar.database.persistence import (
    PendingApprovalRecord,
    ProjectMemory,
    WorkflowRunRecord,
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
