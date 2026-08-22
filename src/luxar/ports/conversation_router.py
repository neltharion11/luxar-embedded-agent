"""Port for choosing whether a message enters the firmware workflow."""

from __future__ import annotations

from typing import Protocol

from luxar.domain.conversation import ConversationDecision


class ConversationRouter(Protocol):
    def route(
        self,
        message: str,
        history: list[dict[str, str]],
        knowledge_status: str | None = None,
        previous_run: dict[str, object] | None = None,
    ) -> ConversationDecision: ...
