"""Validated, evidence-labelled understanding of the current project code."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from luxar.domain.repairs import normalize_project_relative_path


class ProjectEvidenceDecision(BaseModel):
    """Post-source decision about whether external project knowledge is needed."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code_evidence_sufficient: bool = True
    confirmed_from_code: list[str] = Field(default_factory=list, max_length=30)
    missing_evidence: list[str] = Field(default_factory=list, max_length=30)
    knowledge_retrieval: Literal["skip", "retrieve"] = "skip"
    knowledge_query: str = Field(default="", max_length=1000)
    reason: str = Field(default="", max_length=1000)


class ProjectAnalysis(BaseModel):
    """Reusable code analysis identified by a deterministic source fingerprint."""

    model_config = ConfigDict(extra="forbid", strict=True)

    project_exists: bool
    has_source_code: bool
    fingerprint: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=4000)
    entry_points: list[str] = Field(default_factory=list, max_length=20)
    implemented_features: list[str] = Field(default_factory=list, max_length=40)
    architecture: list[str] = Field(default_factory=list, max_length=40)
    gaps: list[str] = Field(default_factory=list, max_length=40)
    risks: list[str] = Field(default_factory=list, max_length=40)
    evidence_paths: list[str] = Field(default_factory=list, max_length=80)
    evidence_decision: ProjectEvidenceDecision = Field(
        default_factory=ProjectEvidenceDecision
    )
    cache_hit: bool = False

    @field_validator("evidence_paths")
    @classmethod
    def validate_evidence_paths(cls, values: list[str]) -> list[str]:
        return [normalize_project_relative_path(value) for value in values]
