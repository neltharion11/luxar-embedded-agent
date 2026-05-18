from __future__ import annotations

from pathlib import Path
from typing import Any

from luxar.skills.loader import load_skill_records
from luxar.skills.matcher import score_skill
from luxar.skills.provenance import default_provenance
from luxar.skills.registry import MarkdownArtifactRecord


class SkillManagerVNext:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def list_skills(self, category: str | None = None) -> list[dict[str, Any]]:
        records = load_skill_records(self.root)
        if category:
            records = [record for record in records if record.category == category]
        return [self._serialize(record) for record in records]

    def view(self, name: str) -> dict[str, Any] | None:
        for record in load_skill_records(self.root):
            if record.name == name:
                return self._serialize(record)
        return None

    def match(self, task: str, limit: int = 3) -> list[dict[str, Any]]:
        scored: list[tuple[int, MarkdownArtifactRecord]] = []
        for record in load_skill_records(self.root):
            score = score_skill(task, record)
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [self._serialize(record, score=score) for score, record in scored[:limit]]

    def executable_skills(self, category: str | None = None) -> list[dict[str, Any]]:
        return [
            item for item in self.list_skills(category=category)
            if item.get("metadata", {}).get("mode") == "executable"
        ]

    def manage(
        self,
        action: str,
        name: str,
        category: str = "workflows",
        content: str = "",
        old_string: str = "",
        new_string: str = "",
    ) -> dict[str, Any]:
        skill_dir = self.root / category / name
        skill_path = skill_dir / "SKILL.md"
        if action == "create":
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(content or self._template(name=name, category=category), encoding="utf-8")
            return {"success": True, "action": action, "path": str(skill_path)}
        if not skill_path.exists():
            return {"success": False, "error": f"Skill '{name}' not found in category '{category}'."}
        if action == "edit":
            skill_path.write_text(content, encoding="utf-8")
        elif action == "patch":
            original = skill_path.read_text(encoding="utf-8")
            if old_string not in original:
                return {"success": False, "error": f"Patch target not found in skill '{name}'."}
            skill_path.write_text(original.replace(old_string, new_string, 1), encoding="utf-8")
        elif action == "archive":
            archive_dir = self.root / "_archived" / category / name
            archive_dir.parent.mkdir(parents=True, exist_ok=True)
            skill_path.replace(archive_dir / "SKILL.md")
        else:
            return {"success": False, "error": f"Unsupported action '{action}'."}
        return {"success": True, "action": action, "path": str(skill_path)}

    def _serialize(self, record: MarkdownArtifactRecord, score: int | None = None) -> dict[str, Any]:
        payload = {
            "name": record.name,
            "category": record.category,
            "title": record.title,
            "path": str(record.path),
            "metadata": record.metadata | {"provenance": default_provenance()},
            "content": record.content,
        }
        if score is not None:
            payload["score"] = score
        return payload

    def _template(self, name: str, category: str) -> str:
        return f"""---
name: {name}
category: {category}
mode: workflow
promotion_level: draft
triggers: []
verification: []
related_lessons: []
references: []
---

# {name}

## When To Use

Describe the trigger conditions for this skill.

## Procedure

1. Inspect the relevant context.
2. Load or create the relevant executable or recovery skill before integration if runtime validation is needed.
3. Capture evidence and update lessons if needed.

## Pitfalls

- Add discovered failure modes here.

## Verification

- Define the required evidence before promotion.
"""
