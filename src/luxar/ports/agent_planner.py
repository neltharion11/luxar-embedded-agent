"""Port for translating a natural-language project goal into Agent intent."""

from __future__ import annotations

from typing import Protocol

from luxar.domain.agent.changes import ObjectiveInterpretation
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_model import ProjectModel


class AgentPlannerPort(Protocol):
    def interpret_goal(
        self,
        task_text: str,
        project_model: ProjectModel,
        current_objective: ProjectObjective | None = None,
    ) -> ObjectiveInterpretation: ...


__all__ = ["AgentPlannerPort"]
