"""Deterministic execution context for tools selected by the Agent model."""

from __future__ import annotations

from collections.abc import Callable
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
    # 长耗时工具（如知识库导入）通过它把阶段进度发回会话流，避免 UI
    # 长时间无事件。签名与 event_reporter 一致：(event_name, data)。
    progress_reporter: Callable[[str, dict[str, object]], None] | None = None


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

    def get_tool_execution(
        self,
        idempotency_key: str,
    ) -> ToolExecutionRecord | None:
        """Read-only probe: return the ledger record for a key, or None."""
        ...


__all__ = [
    "AgentToolExecutionContext",
    "AgentToolPort",
    "ToolExecutionLedgerPort",
]
