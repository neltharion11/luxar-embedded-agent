"""Conversation-first LangGraph state for the continuous Agent runtime."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.tasks import AgentTaskGraph
from luxar.domain.continuous_agent.events import (
    ConversationEvent,
    merge_conversation_events,
)
from luxar.domain.continuous_agent.failures import ContinuousAgentFailure
from luxar.domain.continuous_agent.requests import PendingRequest
from luxar.domain.continuous_agent.tools import ToolCallState
from luxar.domain.continuous_agent.steps import AgentStep


SessionLifecycle = Literal["active", "archived"]
TurnLifecycle = Literal[
    "running",
    "waiting_input",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
]
ObjectiveLifecycle = Literal[
    "none",
    "proposed",
    "active",
    "blocked",
    "completed",
    "abandoned",
]


class ContinuousAgentState(TypedDict, total=False):
    session_id: str
    turn_id: str
    project_key: str
    session_status: SessionLifecycle
    turn_status: TurnLifecycle
    objective_status: ObjectiveLifecycle
    events: Annotated[list[ConversationEvent], merge_conversation_events]
    # 每轮注入的历史对话（user/assistant），属于"输入"而非"状态"：
    # 无 reducer，每轮由 initial_state 整体覆盖，不跨轮累积。
    conversation_history: list[ConversationEvent]
    context_summary: str
    compaction_cursor: int
    active_objective: ProjectObjective
    objective_revision: int
    pending_request: PendingRequest | None
    resolved_inputs: dict[str, object]
    tool_calls: dict[str, ToolCallState]
    tool_approvals: dict[str, bool]
    domain_calls: dict[str, dict[str, object]]
    domain_approvals: dict[str, bool]
    domain_approval_feedback: dict[str, str]
    task_graph: AgentTaskGraph
    evidence_ids: list[str]
    last_failure: ContinuousAgentFailure | None
    step_count: int
    max_steps: int
    cancel_requested: bool
    response_status: Literal["complete", "degraded"]
    next_step: AgentStep


__all__ = [
    "ContinuousAgentState",
    "ObjectiveLifecycle",
    "SessionLifecycle",
    "TurnLifecycle",
]
