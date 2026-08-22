"""Validated references to examples shipped with the active ESP-IDF SDK."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from luxar.domain.repairs import normalize_project_relative_path


class EspIdfExampleReference(BaseModel):
    """Small metadata record safe to keep in workflow state and checkpoints."""

    path: str
    score: int = Field(ge=1)
    matched_terms: list[str] = Field(default_factory=list)
    summary: str = ""

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return normalize_project_relative_path(value)
