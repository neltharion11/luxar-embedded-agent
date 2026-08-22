"""Application persistence ports and PostgreSQL implementation.

LangGraph owns its checkpoint tables. These repositories deliberately keep
queryable application records in separate ``luxar_*`` tables.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


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


@dataclass(frozen=True)
class WorkflowRunRecord:
    thread_id: str
    task_key: str
    task_text: str
    status: str
    result: dict[str, object]


class PersistencePort(Protocol):
    durable: bool

    def health(self) -> bool: ...

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
        chunks: list[tuple[str, int, list[float]]],
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

    def health(self) -> bool:
        return True

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

    def start_run(self, **values: object) -> None:
        with self._lock:
            self._runs[str(values["thread_id"])] = dict(values)

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
            connection.execute(
                "DELETE FROM luxar_conversation_messages WHERE task_key = %s",
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

    def replace_document(
        self,
        *,
        document_id: str,
        project_key: str,
        source_uri: str,
        title: str,
        content_hash: str,
        metadata: dict[str, object],
        chunks: list[tuple[str, int, list[float]]],
    ) -> None:
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
                for ordinal, (content, token_count, embedding) in enumerate(chunks):
                    connection.execute(
                        """
                        INSERT INTO luxar_knowledge_chunks
                            (document_id, ordinal, content, token_count, embedding)
                        VALUES (%s, %s, %s, %s, %s::vector)
                        """,
                        (
                            document_id,
                            ordinal,
                            content,
                            token_count,
                            "[" + ",".join(str(value) for value in embedding) + "]",
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
                score=float(row[5]),
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
