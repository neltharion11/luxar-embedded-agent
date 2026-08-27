"""Port for rolling summaries of early continuous-Agent conversation events."""

from __future__ import annotations

from typing import Protocol

from luxar.domain.continuous_agent.events import ConversationEvent


class AgentContextCompactorPort(Protocol):
    def compact_context(
        self,
        *,
        previous_summary: str,
        events: list[ConversationEvent],
    ) -> str: ...


__all__ = ["AgentContextCompactorPort"]
