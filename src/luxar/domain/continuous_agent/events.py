"""JSON-safe event records accumulated in a continuous Agent checkpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ConversationEventKind = Literal[
    "user_message",
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
    """Append unseen events and reject conflicting reuse of an event ID."""

    merged = list(current or [])
    positions = {item.event_id: index for index, item in enumerate(merged)}
    for event in updates or []:
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
