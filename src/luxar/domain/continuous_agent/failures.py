"""Failure taxonomy that keeps internal errors separate from missing input."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ContinuousAgentFailureCategory = Literal[
    "model",
    "tool",
    "policy",
    "validation",
    "user_input",
    "internal",
]


class ContinuousAgentFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    category: ContinuousAgentFailureCategory
    message: str = Field(min_length=1, max_length=4_000)
    code: str = Field(default="", max_length=160)
    retryable: bool = False
    details: dict[str, object] = Field(default_factory=dict)


__all__ = ["ContinuousAgentFailure", "ContinuousAgentFailureCategory"]
