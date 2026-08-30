"""Port for extracting self-contained facts from source documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from luxar.document_reader import PdfBatch
from luxar.domain.knowledge_atoms import KnowledgeAtomDraft, ParameterAtomDraft


@dataclass(frozen=True)
class KnowledgeExtraction:
    """一次提取的产物：散文型知识原子 + 参数型原子（面向代码生成）。"""

    atoms: list[KnowledgeAtomDraft] = field(default_factory=list)
    parameters: list[ParameterAtomDraft] = field(default_factory=list)


class KnowledgeAtomExtractor(Protocol):
    def extract(
        self,
        *,
        title: str,
        source_uri: str,
        batches: Sequence[PdfBatch],
    ) -> KnowledgeExtraction: ...


__all__ = ["KnowledgeAtomExtractor", "KnowledgeExtraction"]
