from __future__ import annotations

from pathlib import Path

from luxar.memory.transcript_store import TranscriptStore


class SessionSearch:
    def __init__(self, workspace_projects_root: str | Path):
        self._store = TranscriptStore(workspace_projects_root)

    def search(self, query: str, project: str = "", limit: int = 5) -> list[dict]:
        return self._store.search(query=query, project=project, limit=limit)
