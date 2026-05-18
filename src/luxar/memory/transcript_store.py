from __future__ import annotations

from pathlib import Path

from luxar.core.conversation_store import ConversationStore


class TranscriptStore:
    def __init__(self, workspace_projects_root: str | Path):
        self._store = ConversationStore(workspace_projects_root)

    def search(self, query: str, project: str = "", limit: int = 5) -> list[dict]:
        return self._store.search(query=query, project=project or None, limit=limit)
