"""JSON-safe event records accumulated in a continuous Agent checkpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ConversationEventKind = Literal[
    "user_message",
    "assistant_commentary",
    "assistant_message",
    "tool_call",
    "tool_result",
    "objective_updated",
    "pending_request",
    "approval_decision",
    "failure",
    "context_summary",
]


class ConversationEvent(BaseModel):
    """An idempotent, replayable event owned by the application boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event_id: str = Field(min_length=1, max_length=160)
    turn_id: str = Field(min_length=1, max_length=128)
    kind: ConversationEventKind
    sequence: int = Field(ge=0)
    payload: dict[str, object] = Field(default_factory=dict)


def merge_conversation_events(
    current: list[ConversationEvent] | None,
    updates: list[ConversationEvent] | None,
) -> list[ConversationEvent]:
    """Append unseen events and reject conflicting reuse of an event ID.

    每轮开头的普通 user 消息（payload 无 ``steering`` 标记）代表新一轮次的
    起始：此时丢弃上一轮累积的事件，确保工具调用/结果等执行证据不跨轮泄漏，
    也避免历史注入事件把 events channel 无限撑大。steering 运行中指令带
    ``steering: True`` 标记，不会被误判为新一轮次。
    """
    if not updates:
        return list(current or [])
    merged: list[ConversationEvent] = []
    reset = any(
        event.kind == "user_message"
        and not event.payload.get("steering")
        for event in updates
    )
    if not reset:
        merged = list(current or [])
    positions = {item.event_id: index for index, item in enumerate(merged)}
    for event in updates:
        existing_index = positions.get(event.event_id)
        if existing_index is None:
            positions[event.event_id] = len(merged)
            merged.append(event)
            continue
        if merged[existing_index] != event:
            raise ValueError(f"Conversation event ID conflict: {event.event_id}")
    return merged


__all__ = [
    "ConversationEvent",
    "ConversationEventKind",
    "merge_conversation_events",
]
