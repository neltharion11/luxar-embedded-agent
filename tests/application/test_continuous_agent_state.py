from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from luxar.application.continuous_agent_state import ContinuousAgentState
from luxar.checkpoint_serde import create_checkpoint_serializer
from luxar.domain.continuous_agent.events import ConversationEvent
from luxar.domain.continuous_agent.requests import MissingInputRequest


def _compile_passthrough_graph() -> object:
    builder = StateGraph(ContinuousAgentState)
    builder.add_node("persist", lambda _state: {})
    builder.add_edge(START, "persist")
    builder.add_edge("persist", END)
    return builder.compile(
        checkpointer=InMemorySaver(serde=create_checkpoint_serializer())
    )


def test_state_persists_conversation_without_objective() -> None:
    graph = _compile_passthrough_graph()
    config = {"configurable": {"thread_id": "session-no-objective"}}
    first = ConversationEvent(
        event_id="turn-1:user",
        turn_id="turn-1",
        kind="user_message",
        sequence=1,
        payload={"content": "你好"},
    )
    second = ConversationEvent(
        event_id="turn-2:user",
        turn_id="turn-2",
        kind="user_message",
        sequence=2,
        payload={"content": "串口好了，是 COM4"},
    )

    graph.invoke(
        {
            "session_id": "session-no-objective",
            "turn_id": "turn-1",
            "project_key": "0:test4",
            "session_status": "active",
            "turn_status": "completed",
            "objective_status": "none",
            "events": [first],
            "step_count": 0,
            "max_steps": 20,
        },
        config=config,
    )
    restored = graph.invoke(
        {
            "turn_id": "turn-2",
            "turn_status": "running",
            "events": [second],
        },
        config=config,
    )

    assert restored["session_id"] == "session-no-objective"
    assert restored["objective_status"] == "none"
    assert [item.event_id for item in restored["events"]] == [
        "turn-1:user",
        "turn-2:user",
    ]


def test_pending_input_request_round_trips_through_strict_checkpoint() -> None:
    graph = _compile_passthrough_graph()
    config = {"configurable": {"thread_id": "session-waiting-input"}}
    request = MissingInputRequest(
        request_id="request-port",
        prompt="请提供串口",
        fields=["serial_port"],
        reason="系统无法自动发现串口",
    )

    restored = graph.invoke(
        {
            "session_id": "session-waiting-input",
            "turn_id": "turn-1",
            "project_key": "0:test4",
            "turn_status": "waiting_input",
            "pending_request": request,
            "events": [],
        },
        config=config,
    )

    assert restored["pending_request"] == request
