from __future__ import annotations

from luxar.agent.context_builder import RuntimeWorkspace
from luxar.core.config_manager import ConfigManager
from luxar.memory.lesson_store import LessonStore
from luxar.memory.memory_manager import MemoryManager
from luxar.memory.recall import recall_context


def _manager() -> MemoryManager:
    workspace = RuntimeWorkspace.from_manager(ConfigManager())
    workspace.ensure_layout()
    return MemoryManager(workspace.memory_root)


def _lesson_store() -> LessonStore:
    workspace = RuntimeWorkspace.from_manager(ConfigManager())
    workspace.ensure_layout()
    return LessonStore(workspace.lesson_root)


def memory_read(target: str = "memory") -> dict[str, object]:
    return {"success": True, **_manager().read(target=target)}


def memory_write(content: str, target: str = "memory", append: bool = True) -> dict[str, object]:
    return _manager().write(content=content, target=target, append=append)


def memory_search(query: str) -> dict[str, object]:
    workspace = RuntimeWorkspace.from_manager(ConfigManager())
    workspace.ensure_layout()
    return {
        "success": True,
        "results": recall_context(
            query=query,
            memory_root=workspace.memory_root,
            workspace_projects_root=workspace.projects_root,
            lesson_root=workspace.lesson_root,
        ),
    }


def memory_lessons(query: str = "", limit: int = 5) -> dict[str, object]:
    if query.strip():
        return {"success": True, "lessons": _lesson_store().search(query=query, limit=limit)}
    return {"success": True, "lessons": _lesson_store().list_lessons()}


def memory_lesson_record(payload: dict[str, object], promoted: bool = False) -> dict[str, object]:
    return {"success": True, "lesson": _lesson_store().record(payload=payload, promoted=promoted)}


def memory_lesson_promote(slug: str, evidence_count: int = 1) -> dict[str, object]:
    return _lesson_store().promote(slug=slug, evidence_count=evidence_count)
