from __future__ import annotations

from luxar.skills.registry import MarkdownArtifactRecord


def score_skill(task: str, record: MarkdownArtifactRecord) -> int:
    haystack = f"{record.name} {record.category} {record.content} {record.metadata}".lower()
    tokens = task.lower().split()
    # CJK: extract individual characters and bigrams as extra tokens
    cjk_chars = [ch for ch in task if '\u4e00' <= ch <= '\u9fff']
    tokens.extend(cjk_chars)
    tokens.extend(''.join(pair) for pair in zip(cjk_chars, cjk_chars[1:]))
    score = 0
    for token in tokens:
        if token and token in haystack:
            score += 1
    return score