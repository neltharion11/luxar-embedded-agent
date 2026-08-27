from __future__ import annotations

import pytest

from luxar.domain.continuous_agent.events import (
    ConversationEvent,
    merge_conversation_events,
)
from luxar.domain.continuous_agent.failures import ContinuousAgentFailure
from luxar.domain.continuous_agent.requests import (
    MissingInputRequest,
    ToolApprovalRequest,
)
from luxar.domain.continuous_agent.tools import ToolCallState


def _event(event_id: str, sequence: int) -> ConversationEvent:
    return ConversationEvent(
        event_id=event_id,
        turn_id="turn-1",
        kind="user_message",
        sequence=sequence,
        payload={"content": "继续"},
    )


def test_event_reducer_is_idempotent_and_ordered() -> None:
    first = _event("event-1", 1)
    second = _event("event-2", 2)

    merged = merge_conversation_events([first], [first, second])

    assert merged == [first, second]


def test_event_reducer_rejects_conflicting_event_identity() -> None:
    first = _event("event-1", 1)
    conflict = first.model_copy(update={"payload": {"content": "换 COM5"}})

    with pytest.raises(ValueError, match="event-1"):
        merge_conversation_events([first], [conflict])


def test_pending_requests_distinguish_input_from_approval() -> None:
    missing = MissingInputRequest(
        request_id="request-port",
        prompt="请提供开发板串口",
        fields=["serial_port"],
        reason="系统未发现可用串口",
    )
    approval = ToolApprovalRequest(
        request_id="request-flash",
        call_id="call-flash",
        tool_name="device.flash",
        summary="将固件写入 COM4",
        risk="device",
    )

    assert missing.kind == "missing_input"
    assert approval.kind == "approval"


def test_tool_failure_is_not_implicitly_user_input() -> None:
    failure = ContinuousAgentFailure(
        category="tool",
        code="serial_busy",
        message="COM4 被其他进程占用",
        retryable=True,
    )
    call = ToolCallState(
        call_id="call-monitor",
        tool_name="device.monitor",
        arguments={"serial_port": "COM4"},
        status="failed",
        idempotency_key="session-1:turn-1:call-monitor",
        failure=failure,
    )

    assert call.failure is not None
    assert call.failure.category == "tool"
