from __future__ import annotations

from luxar.agent.context_builder import RuntimeWorkspace
from luxar.core.config_manager import ConfigManager
from luxar.memory.lesson_store import LessonStore


def _store() -> LessonStore:
    workspace = RuntimeWorkspace.from_manager(ConfigManager())
    workspace.ensure_layout()
    return LessonStore(workspace.lesson_root)


def lessons_list() -> dict[str, object]:
    return {"success": True, "lessons": _store().list_lessons()}


def lesson_search(query: str, limit: int = 5) -> dict[str, object]:
    return {"success": True, "lessons": _store().search(query=query, limit=limit)}


def lesson_record(payload: dict[str, object], promoted: bool = False) -> dict[str, object]:
    return {"success": True, "lesson": _store().record(payload=payload, promoted=promoted)}


def lesson_promote(slug: str, evidence_count: int = 1) -> dict[str, object]:
    return _store().promote(slug=slug, evidence_count=evidence_count)
