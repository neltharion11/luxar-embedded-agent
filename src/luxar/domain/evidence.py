from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BuildEvidence(BaseModel):
    success: bool
    command: list[str] = Field(min_length=1)
    return_code: int
    stdout_summary: str = ""
    stderr_summary: str = ""
    error_category: Literal[
        "environment",
        "source",
        "linker",
        "timeout",
        "unknown",
    ] | None = None

    @model_validator(mode="after")
    def validate_result_consistency(self) -> BuildEvidence:
        if self.success and self.return_code != 0:
            raise ValueError(
                "successful build evidence must have return_code 0"
            )

        if not self.success and self.return_code == 0:
            raise ValueError(
                "failed build evidence cannot have return_code 0"
            )

        if self.success and self.error_category is not None:
            raise ValueError(
                "successful build evidence cannot have an error category"
            )

        return self