"""Port for bounded, resumable domain workflows selected by the top Agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.continuous_agent.requests import ToolApprovalRequest
from luxar.domain.continuous_agent.steps import DomainWorkflowCall


class DomainWorkflowDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=2_000)


@dataclass(frozen=True)
class DomainWorkflowExecutionContext:
    session_id: str
    turn_id: str
    project_key: str
    project_path: Path | None = None
    event_reporter: Callable[[str, dict[str, object]], None] | None = None


@dataclass(frozen=True)
class DomainWorkflowOutcome:
    status: Literal[
        "completed",
        "waiting_input",
        "waiting_approval",
        "blocked",
        "failed",
    ]
    summary: str
    result: dict[str, object]
    pending_approval: ToolApprovalRequest | None = None


class DomainWorkflowPort(Protocol):
    descriptor: DomainWorkflowDescriptor

    def start(
        self,
        call: DomainWorkflowCall,
        context: DomainWorkflowExecutionContext,
    ) -> DomainWorkflowOutcome: ...

    def resume(
        self,
        call: DomainWorkflowCall,
        context: DomainWorkflowExecutionContext,
        *,
        approved: bool,
        feedback: str = "",
    ) -> DomainWorkflowOutcome: ...


__all__ = [
    "DomainWorkflowDescriptor",
    "DomainWorkflowExecutionContext",
    "DomainWorkflowOutcome",
    "DomainWorkflowPort",
]
