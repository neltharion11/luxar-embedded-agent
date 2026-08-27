"""Port for user-visible, evidence-bounded streaming Agent replies."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from luxar.domain.continuous_agent.steps import AgentStepContext


class AgentReplyStreamerPort(Protocol):
    def stream_reply(
        self,
        *,
        draft: str,
        context: AgentStepContext,
    ) -> Iterable[str]: ...


__all__ = ["AgentReplyStreamerPort"]
