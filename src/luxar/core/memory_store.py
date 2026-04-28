"""SQLite + FTS5 persistent memory store for conversation history."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    source TEXT NOT NULL,
    model TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    project TEXT,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    reasoning_content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(session_id, role);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


class MemoryStore:
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path.home() / ".luxar" / "memory.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=2.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self):
        cursor = self._conn.cursor()
        cursor.executescript(SCHEMA_SQL)
        self._ensure_schema_migrations(cursor)
        try:
            cursor.execute("SELECT * FROM messages_fts LIMIT 0")
        except sqlite3.OperationalError:
            cursor.executescript(FTS_SQL)
        self._conn.commit()

    def _ensure_schema_migrations(self, cursor):
        cursor.execute("PRAGMA table_info(messages)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "reasoning_content" not in columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN reasoning_content TEXT")

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    def _execute_write(self, fn):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn(self._conn)
                self._conn.commit()
            except Exception:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                raise
        return result

    # Session management
    def ensure_session(self, session_id: str, source: str = "web",
                       project: str = None) -> None:
        def _do(conn):
            conn.execute(
                """INSERT OR IGNORE INTO sessions (id, source, project, started_at)
                   VALUES (?, ?, ?, ?)""",
                (session_id, source, project, time.time()),
            )
        self._execute_write(_do)

    def end_session(self, session_id: str, reason: str = "complete"):
        def _do(conn):
            conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ? AND ended_at IS NULL",
                (time.time(), reason, session_id),
            )
        self._execute_write(_do)

    def create_continuation(self, session_id: str, source: str = "web") -> str:
        """Create a new session that continues from a compressed parent."""
        new_id = f"{session_id}_c{int(time.time())}"
        def _do(conn):
            conn.execute(
                """INSERT INTO sessions (id, source, parent_session_id, project, started_at)
                   VALUES (?, ?, ?, (SELECT project FROM sessions WHERE id = ?), ?)""",
                (new_id, source, session_id, session_id, time.time()),
            )
            conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = 'compression' WHERE id = ?",
                (time.time(), session_id),
            )
        self._execute_write(_do)
        return new_id

    # Message storage
    def append_message(self, session_id: str, role: str, content: str = None,
                       tool_call_id: str = None, tool_name: str = None,
                       tool_calls=None, token_count: int = None,
                       reasoning_content: str = None) -> int:
        tc_json = json.dumps(tool_calls) if tool_calls else None

        def _do(conn):
            cursor = conn.execute(
                """INSERT INTO messages (session_id, role, content, reasoning_content, tool_call_id,
                   tool_calls, tool_name, timestamp, token_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, role, content, reasoning_content, tool_call_id, tc_json,
                 tool_name, time.time(), token_count),
            )
            msg_id = cursor.lastrowid
            conn.execute(
                "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?",
                (session_id,),
            )
            return msg_id
        return self._execute_write(_do)

    def append_messages_batch(self, session_id: str, messages: list[dict]):
        """Batch-append messages for bulk import (replaces existing messages)."""
        def _do(conn):
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            for m in messages:
                tc = json.dumps(m.get("tool_calls")) if m.get("tool_calls") else None
                conn.execute(
                    """INSERT INTO messages (session_id, role, content, reasoning_content, tool_call_id,
                       tool_calls, tool_name, timestamp, token_count)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (session_id, m.get("role"), m.get("content"),
                     m.get("reasoning_content"), m.get("tool_call_id"), tc, m.get("tool_name"),
                     time.time(), None),
                )
            conn.execute(
                "UPDATE sessions SET message_count = ? WHERE id = ?",
                (len(messages), session_id),
            )
        self._execute_write(_do)

    def get_messages(self, session_id: str) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT role, content, reasoning_content, tool_call_id, tool_calls, tool_name "
                "FROM messages WHERE session_id = ? ORDER BY timestamp, id",
                (session_id,),
            )
            rows = cursor.fetchall()
        result = []
        for row in rows:
            msg = {"role": row["role"], "content": row["content"]}
            if row["reasoning_content"]:
                msg["reasoning_content"] = row["reasoning_content"]
            if row["tool_call_id"]:
                msg["tool_call_id"] = row["tool_call_id"]
            if row["tool_name"]:
                msg["tool_name"] = row["tool_name"]
            if row["tool_calls"]:
                try:
                    msg["tool_calls"] = json.loads(row["tool_calls"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(msg)
        return result

    # FTS5 search
    def search(self, query: str, project: str = None, limit: int = 5) -> list[dict]:
        """Full-text search across all message contents, optionally scoped to a project."""
        sanitized = query.replace('"', '""')[:200]
        if project:
            sql = """SELECT m.role, m.content, m.timestamp, s.project
                     FROM messages_fts f
                     JOIN messages m ON f.rowid = m.id
                     JOIN sessions s ON m.session_id = s.id
                     WHERE messages_fts MATCH ? AND s.project = ?
                     ORDER BY rank LIMIT ?"""
            params = (f'"{sanitized}"', project, limit)
        else:
            sql = """SELECT m.role, m.content, m.timestamp, s.project
                     FROM messages_fts f
                     JOIN messages m ON f.rowid = m.id
                     JOIN sessions s ON m.session_id = s.id
                     WHERE messages_fts MATCH ?
                     ORDER BY rank LIMIT ?"""
            params = (f'"{sanitized}"', limit)
        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
            except sqlite3.OperationalError:
                return []
            rows = cursor.fetchall()
        return [
            {"role": r["role"], "content": (r["content"] or "")[:300],
             "timestamp": r["timestamp"], "project": r["project"]}
            for r in rows
        ]

    def list_sessions(self, project: str = None, limit: int = 20) -> list[dict]:
        where = "WHERE ended_at IS NULL AND parent_session_id IS NULL"
        params = []
        if project:
            where += " AND project = ?"
            params.append(project)
        with self._lock:
            cursor = self._conn.execute(
                f"SELECT id, source, project, started_at, message_count, title "
                f"FROM sessions {where} ORDER BY started_at DESC LIMIT ?",
                params + [limit],
            )
            rows = cursor.fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: str):
        def _do(conn):
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._execute_write(_do)
