"""Port for one semantic decision in the continuous Agent loop."""

from __future__ import annotations

from typing import Protocol

from luxar.domain.continuous_agent.steps import AgentStep, AgentStepContext


class AgentStepPort(Protocol):
    def decide_next_step(self, context: AgentStepContext) -> AgentStep: ...


__all__ = ["AgentStepPort"]
