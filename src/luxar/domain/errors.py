from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class WorkflowError(BaseModel):
    stage: Literal[
        "requirement_analysis",
        "planning",
        "project_creation",
        "build",
    ]
    category: Literal[
        "model_output",
        "environment",
        "source",
        "linker",
        "timeout",
        "unknown",
    ]
    message: str
    retryable: bool
    user_suggestion: str = ""
    evidence_reference: str | None = None