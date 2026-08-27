"""Port for synthesizing a direct answer from approved evidence only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from luxar.domain.knowledge_answers import GroundedKnowledgeAnswer, KnowledgeEvidence


class KnowledgeAnswerer(Protocol):
    def answer(
        self,
        *,
        question: str,
        evidence: Sequence[KnowledgeEvidence],
        revision_instructions: str = "",
        response_plan: Mapping[str, object] | None = None,
        conversation_context: Sequence[Mapping[str, str]] | None = None,
    ) -> GroundedKnowledgeAnswer: ...


__all__ = ["KnowledgeAnswerer"]
