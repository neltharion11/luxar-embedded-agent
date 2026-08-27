"""Checkpoint-safe lifecycle for a single Agent tool call."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.continuous_agent.failures import ContinuousAgentFailure


ToolCallStatus = Literal[
    "proposed",
    "waiting_approval",
    "approved",
    "running",
    "succeeded",
    "failed",
    "rejected",
    "indeterminate",
]
ToolExecutionLedgerStatus = Literal[
    "running",
    "succeeded",
    "failed",
    "rejected",
    "indeterminate",
]


class ToolCallState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    call_id: str = Field(min_length=1, max_length=160)
    tool_name: str = Field(min_length=1, max_length=240)
    arguments: dict[str, object] = Field(default_factory=dict)
    status: ToolCallStatus = "proposed"
    idempotency_key: str = Field(min_length=1, max_length=512)
    result: dict[str, object] | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    failure: ContinuousAgentFailure | None = None


class ToolResult(BaseModel):
    """Normalized tool output returned to the model and evidence layer."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    success: bool
    output: dict[str, object] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    failure: ContinuousAgentFailure | None = None


class ToolExecutionRecord(BaseModel):
    """Durable exactly-once record for a side-effect-capable tool call."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    idempotency_key: str = Field(min_length=1, max_length=512)
    session_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    call_id: str = Field(min_length=1, max_length=160)
    tool_name: str = Field(min_length=1, max_length=240)
    arguments_fingerprint: str = Field(min_length=1, max_length=8_000)
    status: ToolExecutionLedgerStatus
    result: dict[str, object] | None = None
    failure: ContinuousAgentFailure | None = None


__all__ = [
    "ToolCallState",
    "ToolCallStatus",
    "ToolExecutionLedgerStatus",
    "ToolExecutionRecord",
    "ToolResult",
]
