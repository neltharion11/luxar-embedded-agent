from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_yaml = YAML(typ="safe")


class LessonStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.draft_root = self.root / "draft"
        self.promoted_root = self.root / "promoted"
        self.draft_root.mkdir(parents=True, exist_ok=True)
        self.promoted_root.mkdir(parents=True, exist_ok=True)

    def _paths(self) -> list[Path]:
        return sorted(self.draft_root.glob("*.yaml")) + sorted(self.promoted_root.glob("*.yaml"))

    def list_lessons(self) -> list[dict[str, Any]]:
        return [self._load(path) for path in self._paths()]

    def _load(self, path: Path) -> dict[str, Any]:
        payload = _yaml.load(path.read_text(encoding="utf-8")) or {}
        payload["path"] = str(path)
        payload["state"] = "promoted" if path.parent == self.promoted_root else "draft"
        return payload

    def _validate_lesson_schema(self, payload: dict[str, Any]) -> None:
        required_fields = ["topic", "symptom", "hypothesis", "evidence", "resolution", "outcome"]
        missing = [f for f in required_fields if not str(payload.get(f) or "").strip()]
        if missing:
            raise ValueError(f"Invalid lesson schema. Missing required fields: {', '.join(missing)}")

    def record(self, payload: dict[str, Any], promoted: bool = False) -> dict[str, Any]:
        self._validate_lesson_schema(payload)
        slug = str(payload.get("slug") or payload.get("topic") or "lesson").strip().replace(" ", "-").lower()
        target_root = self.promoted_root if promoted else self.draft_root
        path = target_root / f"{slug}.yaml"
        path.write_text(self._dump_yaml(payload), encoding="utf-8")
        return self._load(path)

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        query_norm = query.strip().lower()
        matches: list[dict[str, Any]] = []
        for path in self._paths():
            payload = self._load(path)
            blob = " ".join(str(payload.get(key, "")) for key in ("topic", "symptom", "hypothesis", "resolution"))
            if not query_norm or query_norm in blob.lower():
                matches.append(payload)
        return matches[:limit]

    def promote(self, slug: str, evidence_count: int) -> dict[str, Any]:
        source = self.draft_root / f"{slug}.yaml"
        if not source.exists():
            return {"success": False, "error": f"Lesson '{slug}' not found in draft state."}
        payload = self._load(source)
        payload["evidence_count"] = evidence_count
        
        try:
            self._validate_lesson_schema(payload)
        except ValueError as e:
            return {"success": False, "error": str(e)}

        target = self.promoted_root / source.name
        target.write_text(self._dump_yaml(payload), encoding="utf-8")
        source.unlink()
        return self._load(target)

    def _dump_yaml(self, payload: dict[str, Any]) -> str:
        from io import StringIO

        stream = StringIO()
        _yaml.dump(payload, stream)
        return stream.getvalue()
