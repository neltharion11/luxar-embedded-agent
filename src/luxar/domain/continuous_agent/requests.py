"""The only two user-facing reasons a continuous Agent may pause."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class MissingInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["missing_input"] = "missing_input"
    request_id: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=4_000)
    fields: list[str] = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=2_000)


class ToolApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["approval"] = "approval"
    request_id: str = Field(min_length=1, max_length=160)
    call_id: str = Field(min_length=1, max_length=160)
    tool_name: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=4_000)
    risk: Literal["write", "device", "download", "destructive"]


PendingRequest = Annotated[
    MissingInputRequest | ToolApprovalRequest,
    Field(discriminator="kind"),
]


__all__ = ["MissingInputRequest", "PendingRequest", "ToolApprovalRequest"]
