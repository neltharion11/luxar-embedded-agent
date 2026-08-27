"""Semantic knowledge units indexed independently from document pagination."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeAtomDraft(BaseModel):
    """One self-contained fact extracted from an untrusted source document."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=240)
    statement: str = Field(min_length=1, max_length=4000)
    category: str = Field(default="general", min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    applicable_conditions: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    source_pages: list[int] = Field(default_factory=list, max_length=30)
    source_section: str | None = Field(default=None, max_length=300)
    source_excerpt: str = Field(default="", max_length=4000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator(
        "subject",
        "statement",
        "category",
        "source_section",
        "source_excerpt",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("aliases", "applicable_conditions", "limitations")
    @classmethod
    def _clean_text_list(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @field_validator("source_pages")
    @classmethod
    def _clean_pages(cls, values: list[int]) -> list[int]:
        return sorted({value for value in values if value > 0})


class KnowledgeAtomExtraction(BaseModel):
    """Structured response contract used by model-backed extractors."""

    model_config = ConfigDict(extra="forbid")

    atoms: list[KnowledgeAtomDraft] = Field(default_factory=list, max_length=300)


@dataclass(frozen=True)
class KnowledgeAtom:
    """Validated knowledge persisted as the searchable unit."""

    knowledge_id: str
    subject: str
    statement: str
    category: str
    aliases: tuple[str, ...] = ()
    applicable_conditions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    source_document_id: str = ""
    source_uri: str = ""
    source_title: str = ""
    source_pages: tuple[int, ...] = ()
    source_section: str | None = None
    source_excerpt: str = ""
    confidence: float = 1.0

    def searchable_text(self) -> str:
        """Build the embedding text from knowledge, never from a page label."""

        parts = [f"主题：{self.subject}", f"知识：{self.statement}"]
        if self.category:
            parts.append(f"类别：{self.category}")
        if self.aliases:
            parts.append("别名：" + "、".join(self.aliases))
        if self.applicable_conditions:
            parts.append("适用条件：" + "；".join(self.applicable_conditions))
        if self.limitations:
            parts.append("限制：" + "；".join(self.limitations))
        return "\n".join(parts)

    def metadata(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "unit_type": "knowledge_atom",
            "knowledge_id": self.knowledge_id,
            "subject": self.subject,
            "category": self.category,
            "aliases": list(self.aliases),
            "applicable_conditions": list(self.applicable_conditions),
            "limitations": list(self.limitations),
            "source_document_id": self.source_document_id,
            "source_pages": list(self.source_pages),
            "source_section": self.source_section,
            "source_excerpt": self.source_excerpt,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class KnowledgeChunk:
    """Storage-neutral indexed record used by knowledge backends."""

    content: str
    token_count: int
    embedding: list[float]
    metadata: dict[str, object] = field(default_factory=dict)


_SPACE_RE = re.compile(r"\s+")


def _identity_text(draft: KnowledgeAtomDraft) -> str:
    return "|".join(
        [
            _SPACE_RE.sub(" ", draft.subject).strip().casefold(),
            _SPACE_RE.sub(" ", draft.statement).strip().casefold(),
            "|".join(value.casefold() for value in draft.applicable_conditions),
            "|".join(value.casefold() for value in draft.limitations),
        ]
    )


def materialize_knowledge_atoms(
    drafts: Sequence[KnowledgeAtomDraft],
    *,
    document_id: str,
    source_uri: str,
    source_title: str,
) -> list[KnowledgeAtom]:
    """Validate, deduplicate and attach provenance to extracted facts."""

    atoms: list[KnowledgeAtom] = []
    seen: set[str] = set()
    for draft in drafts:
        identity = _identity_text(draft)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        atoms.append(
            KnowledgeAtom(
                knowledge_id=f"ka-{digest[:24]}",
                subject=draft.subject,
                statement=draft.statement,
                category=draft.category,
                aliases=tuple(draft.aliases),
                applicable_conditions=tuple(draft.applicable_conditions),
                limitations=tuple(draft.limitations),
                source_document_id=document_id,
                source_uri=source_uri,
                source_title=source_title,
                source_pages=tuple(draft.source_pages),
                source_section=draft.source_section,
                source_excerpt=draft.source_excerpt or draft.statement,
                confidence=draft.confidence,
            )
        )
    return atoms


__all__ = [
    "KnowledgeAtom",
    "KnowledgeAtomDraft",
    "KnowledgeAtomExtraction",
    "KnowledgeChunk",
    "materialize_knowledge_atoms",
]
