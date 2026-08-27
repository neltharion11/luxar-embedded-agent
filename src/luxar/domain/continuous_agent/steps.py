"""Provider-neutral decisions emitted by the continuous Agent model."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.continuous_agent.events import ConversationEvent
from luxar.domain.continuous_agent.requests import MissingInputRequest


class AgentToolDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=2_000)
    input_schema: dict[str, object] = Field(default_factory=dict)
    read_only: bool
    requires_approval: bool


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    call_id: str = Field(min_length=1, max_length=160)
    tool_name: str = Field(min_length=1, max_length=240)
    arguments: dict[str, object] = Field(default_factory=dict)


class AssistantReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["assistant_reply"] = "assistant_reply"
    content: str = Field(min_length=1, max_length=200_000)


class ToolCallBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["tool_calls"] = "tool_calls"
    calls: list[ToolCall] = Field(min_length=1, max_length=8)


class DomainWorkflowCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["domain_workflow"] = "domain_workflow"
    call_id: str = Field(min_length=1, max_length=160)
    workflow_name: str = Field(min_length=1, max_length=240)
    task: str = Field(min_length=1, max_length=20_000)


class AskUser(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["ask_user"] = "ask_user"
    request: MissingInputRequest


class FinishObjective(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["finish_objective"] = "finish_objective"
    outcome: Literal["completed", "abandoned"]
    summary: str = Field(min_length=1, max_length=20_000)


AgentStep = Annotated[
    AssistantReply | ToolCallBatch | DomainWorkflowCall | AskUser | FinishObjective,
    Field(discriminator="type"),
]


class AgentStepEnvelope(BaseModel):
    """Wrapper used to publish one stable JSON Schema to model providers."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    step: AgentStep


class AgentStepContext(BaseModel):
    """Bounded state projection sent to the decision model."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    session_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    project_key: str = Field(min_length=1, max_length=240)
    context_summary: str = Field(default="", max_length=200_000)
    recent_events: list[ConversationEvent] = Field(
        default_factory=list,
        max_length=100,
    )
    active_objective: ProjectObjective | None = None
    pending_request: MissingInputRequest | None = None
    resolved_inputs: dict[str, object] = Field(default_factory=dict)
    tools: list[AgentToolDescriptor] = Field(default_factory=list, max_length=100)
    domain_workflows: list[dict[str, object]] = Field(
        default_factory=list,
        max_length=20,
    )
    latest_tool_results: list[dict[str, object]] = Field(
        default_factory=list,
        max_length=20,
    )
    latest_domain_results: list[dict[str, object]] = Field(
        default_factory=list,
        max_length=10,
    )


__all__ = [
    "AgentStep",
    "AgentStepContext",
    "AgentStepEnvelope",
    "AgentToolDescriptor",
    "AskUser",
    "AssistantReply",
    "DomainWorkflowCall",
    "FinishObjective",
    "ToolCall",
    "ToolCallBatch",
]
