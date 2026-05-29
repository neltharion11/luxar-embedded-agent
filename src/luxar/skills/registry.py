from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_yaml = YAML(typ="safe")


@dataclass(slots=True)
class MarkdownArtifactRecord:
    name: str
    category: str
    path: Path
    title: str
    metadata: dict[str, Any]
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "path": str(self.path),
            "title": self.title,
            "metadata": self.metadata,
            "content": self.content,
        }


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    frontmatter = text[4:end]
    body = text[end + 5 :]
    data = _yaml.load(frontmatter) or {}
    return data, body


def scan_markdown_artifacts(root: Path, marker: str) -> list[MarkdownArtifactRecord]:
    if not root.exists():
        return []
    records: list[MarkdownArtifactRecord] = []
    for path in sorted(root.rglob(marker)):
        text = path.read_text(encoding="utf-8")
        metadata, body = parse_frontmatter(text)
        name = str(metadata.get("name") or path.parent.name)
        category = str(metadata.get("category") or path.parent.parent.name if path.parent.parent != root else "")
        title = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), name)
        records.append(
            MarkdownArtifactRecord(
                name=name,
                category=category,
                path=path,
                title=title,
                metadata=metadata,
                content=body.strip(),
            )
        )
    return records
