"""Grounded-answer contracts for the dedicated knowledge workflow."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(pattern=r"^E\d+$")
    knowledge_id: str | None = None
    document_id: str
    title: str
    source_uri: str
    subject: str
    statement: str
    category: str = "general"
    source_pages: list[int] = Field(default_factory=list)
    source_section: str | None = None
    applicable_conditions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    score: float


class EvidenceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sufficient: bool
    reason: str
    missing_facets: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    document_count: int = 0


class GroundedKnowledgeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_markdown: str = Field(min_length=1, max_length=30_000)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    coverage_summary: str = Field(default="", max_length=2000)
    uncertainties: list[str] = Field(default_factory=list, max_length=30)


class KnowledgeAnswerVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issue_codes: list[str] = Field(default_factory=list)
    invalid_citations: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    revision_instructions: str = Field(default="", max_length=4000)


__all__ = [
    "EvidenceAssessment",
    "GroundedKnowledgeAnswer",
    "KnowledgeAnswerVerification",
    "KnowledgeEvidence",
]
