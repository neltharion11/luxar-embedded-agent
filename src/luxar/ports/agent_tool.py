"""Deterministic execution context for tools selected by the Agent model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from luxar.domain.continuous_agent.steps import AgentToolDescriptor
from luxar.domain.continuous_agent.failures import ContinuousAgentFailure
from luxar.domain.continuous_agent.tools import (
    ToolExecutionLedgerStatus,
    ToolExecutionRecord,
    ToolResult,
)


@dataclass(frozen=True)
class AgentToolExecutionContext:
    session_id: str
    turn_id: str
    project_key: str
    project_path: Path | None = None


class AgentToolPort(Protocol):
    descriptor: AgentToolDescriptor
    input_model: type[BaseModel]

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult: ...


class ToolExecutionLedgerPort(Protocol):
    def reserve_tool_execution(
        self,
        *,
        idempotency_key: str,
        session_id: str,
        turn_id: str,
        call_id: str,
        tool_name: str,
        arguments_fingerprint: str,
    ) -> tuple[ToolExecutionRecord, bool]: ...

    def finish_tool_execution(
        self,
        idempotency_key: str,
        *,
        status: ToolExecutionLedgerStatus,
        result: dict[str, object] | None = None,
        failure: ContinuousAgentFailure | None = None,
    ) -> ToolExecutionRecord: ...


__all__ = [
    "AgentToolExecutionContext",
    "AgentToolPort",
    "ToolExecutionLedgerPort",
]
