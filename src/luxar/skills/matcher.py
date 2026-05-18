from __future__ import annotations

from luxar.skills.registry import MarkdownArtifactRecord


def score_skill(task: str, record: MarkdownArtifactRecord) -> int:
    haystack = f"{record.name} {record.category} {record.content} {record.metadata}".lower()
    score = 0
    for token in task.lower().split():
        if token and token in haystack:
            score += 1
    return score
