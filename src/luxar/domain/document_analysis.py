"""Structured facts distilled from engineering PDFs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DocumentFact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: str = Field(min_length=1, max_length=120)
    fact: str = Field(min_length=1, max_length=4000)
    evidence_pages: list[int] = Field(default_factory=list)


class DocumentSectionAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    relevant: bool
    facts: list[DocumentFact] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class PdfTechnicalReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str = Field(min_length=1, max_length=30_000)
    technical_context: str = Field(min_length=1, max_length=20_000)
    analysis_warnings: list[str] = Field(default_factory=list)
