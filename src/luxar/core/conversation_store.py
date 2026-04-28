"""Persist chat conversations using SQLite MemoryStore with FTS5 search."""

from __future__ import annotations

from pathlib import Path

from luxar.core.memory_store import MemoryStore


class ConversationStore:
    def __init__(self, workspace_root: str | Path):
        ws = Path(workspace_root)
        db_path = ws.parent / ".luxar" / "memory.db" if ws.name == "projects" else ws / ".luxar" / "memory.db"
        self.memory = MemoryStore(db_path)

    def _session_id(self, project: str) -> str:
        return f"project:{project}" if project else "global"

    def load(self, project: str) -> list[dict]:
        sid = self._session_id(project)
        self.memory.ensure_session(sid, source="web", project=project)
        return self.memory.get_messages(sid)

    def save(self, project: str, messages: list[dict]):
        sid = self._session_id(project)
        self.memory.ensure_session(sid, source="web", project=project)
        self.memory.append_messages_batch(sid, messages)

    def delete(self, project: str):
        sid = self._session_id(project)
        self.memory.delete_session(sid)

    def list_projects(self) -> list[str]:
        sessions = self.memory.list_sessions()
        return [s["project"] for s in sessions if s.get("project")]

    def search(self, query: str, project: str = None, limit: int = 5) -> list[dict]:
        return self.memory.search(query, project=project, limit=limit)

    def close(self):
        self.memory.close()
