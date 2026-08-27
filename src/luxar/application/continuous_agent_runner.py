"""Durable runner and approval-resume boundary for the continuous Agent graph."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from luxar.application.continuous_agent_graph import (
    ContinuousAgentRuntimeContext,
    build_continuous_agent_graph,
)
from luxar.application.continuous_agent_state import ContinuousAgentState
from luxar.checkpoint_serde import create_checkpoint_serializer
from luxar.domain.continuous_agent.requests import ToolApprovalRequest


@dataclass(frozen=True)
class ContinuousAgentRunResult:
    state: ContinuousAgentState
    thread_id: str
    pending_approval: ToolApprovalRequest | None = None
    checkpointer: BaseCheckpointSaver | None = None


def _prepare_state(initial_state: ContinuousAgentState) -> ContinuousAgentState:
    prepared = dict(initial_state)
    prepared.setdefault("turn_status", "running")
    prepared.setdefault("events", [])
    prepared.setdefault("step_count", 0)
    prepared.setdefault("max_steps", 40)
    prepared.setdefault("cancel_requested", False)
    return cast(ContinuousAgentState, prepared)


def _drive(
    graph_input: object,
    *,
    context: ContinuousAgentRuntimeContext,
    thread_id: str,
    checkpointer: BaseCheckpointSaver,
    latest_state: ContinuousAgentState,
) -> ContinuousAgentRunResult:
    graph = build_continuous_agent_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    for snapshot in graph.stream(
        graph_input,
        config=config,
        context=context,
        stream_mode="values",
    ):
        if "__interrupt__" in snapshot:
            request = ToolApprovalRequest.model_validate(
                snapshot["__interrupt__"][0].value
            )
            latest_state = cast(
                ContinuousAgentState,
                {
                    key: value
                    for key, value in snapshot.items()
                    if key != "__interrupt__"
                },
            )
            return ContinuousAgentRunResult(
                state=latest_state,
                thread_id=thread_id,
                pending_approval=request,
                checkpointer=checkpointer,
            )
        latest_state = cast(ContinuousAgentState, snapshot)
    return ContinuousAgentRunResult(
        state=latest_state,
        thread_id=thread_id,
        checkpointer=checkpointer,
    )


def run_continuous_agent_workflow(
    *,
    initial_state: ContinuousAgentState,
    context: ContinuousAgentRuntimeContext,
    thread_id: str | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> ContinuousAgentRunResult:
    selected_thread_id = thread_id or initial_state.get("session_id") or uuid.uuid4().hex
    selected_checkpointer = checkpointer or InMemorySaver(
        serde=create_checkpoint_serializer()
    )
    prepared = _prepare_state(initial_state)
    return _drive(
        prepared,
        context=context,
        thread_id=selected_thread_id,
        checkpointer=selected_checkpointer,
        latest_state=prepared,
    )


def resume_continuous_agent_workflow(
    *,
    thread_id: str,
    context: ContinuousAgentRuntimeContext,
    checkpointer: BaseCheckpointSaver,
    approved: bool,
    feedback: str = "",
) -> ContinuousAgentRunResult:
    graph = build_continuous_agent_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    latest_state = cast(
        ContinuousAgentState,
        dict(graph.get_state(config).values or {}),
    )
    return _drive(
        Command(resume={"approved": approved, "feedback": feedback}),
        context=context,
        thread_id=thread_id,
        checkpointer=checkpointer,
        latest_state=latest_state,
    )


__all__ = [
    "ContinuousAgentRunResult",
    "resume_continuous_agent_workflow",
    "run_continuous_agent_workflow",
]
