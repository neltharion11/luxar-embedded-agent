"""Port for extracting self-contained facts from source documents."""

from __future__ import annotations

from typing import Protocol, Sequence

from luxar.document_reader import PdfBatch
from luxar.domain.knowledge_atoms import KnowledgeAtomDraft


class KnowledgeAtomExtractor(Protocol):
    def extract(
        self,
        *,
        title: str,
        source_uri: str,
        batches: Sequence[PdfBatch],
    ) -> list[KnowledgeAtomDraft]: ...


__all__ = ["KnowledgeAtomExtractor"]
