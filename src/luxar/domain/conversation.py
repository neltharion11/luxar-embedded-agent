"""Conversation routing values used before the firmware workflow."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class ConversationDecision(BaseModel):
    """A validated model decision for the conversation entry mode."""

    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Literal[
        "casual_chat",
        "workflow_status",
        "project_inspection",
        "firmware_task",
    ]
    response: str = ""

    @model_validator(mode="after")
    def require_chat_response(self) -> "ConversationDecision":
        self.response = self.response.strip()
        if self.intent in {"casual_chat", "workflow_status"} and not self.response:
            raise ValueError("直接回答模式必须包含回复")
        if self.intent not in {"casual_chat", "workflow_status"}:
            self.response = ""
        return self
