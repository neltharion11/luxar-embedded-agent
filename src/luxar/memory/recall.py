from __future__ import annotations

from pathlib import Path

from luxar.memory.lesson_store import LessonStore
from luxar.memory.memory_manager import MemoryManager
from luxar.memory.session_search import SessionSearch


def recall_context(query: str, memory_root: str | Path, workspace_projects_root: str | Path, lesson_root: str | Path) -> dict[str, object]:
    return {
        "memory": MemoryManager(memory_root).search(query),
        "sessions": SessionSearch(workspace_projects_root).search(query),
        "lessons": LessonStore(lesson_root).search(query),
    }
