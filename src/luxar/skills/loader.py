from __future__ import annotations

from pathlib import Path

from luxar.skills.registry import MarkdownArtifactRecord, scan_markdown_artifacts


def load_skill_records(root: str | Path) -> list[MarkdownArtifactRecord]:
    return scan_markdown_artifacts(Path(root), "SKILL.md")
