"""Stable Session and per-message Turn identities for the continuous Agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


AgentSessionStatus = Literal["active", "archived"]
AgentTurnStatus = Literal[
    "running",
    "waiting_input",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
]


class AgentSession(BaseModel):
    """A durable conversation mapped one-to-one to a LangGraph thread."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    session_id: str = Field(min_length=1, max_length=128)
    project_key: str = Field(min_length=1, max_length=240)
    status: AgentSessionStatus = "active"
    active_objective_id: str | None = Field(default=None, max_length=240)
    context_summary: str = Field(default="", max_length=200_000)
    compaction_cursor: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime

    @field_validator("session_id", "project_key")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent Session 标识不能为空")
        return normalized


class AgentTurn(BaseModel):
    """One user message and its execution lifecycle inside a Session."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    turn_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    client_turn_id: str = Field(min_length=1, max_length=128)
    status: AgentTurnStatus = "running"
    user_message: str = Field(min_length=1, max_length=200_000)
    assistant_message: str = Field(default="", max_length=2_000_000)
    failure: dict[str, object] | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("turn_id", "session_id", "client_turn_id")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent Turn 标识不能为空")
        return normalized
