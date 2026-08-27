"""Port for producing one task-scoped, structured code change bundle."""

from __future__ import annotations

from typing import Protocol

from luxar.domain.agent.code_changes import ChangeBundle
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_model import ProjectModel
from luxar.domain.agent.tasks import AgentTask
from luxar.domain.evidence import BuildEvidence
from luxar.domain.repairs import ProjectFile


class CodeEngineerPort(Protocol):
    def create_bundle(
        self,
        objective: ProjectObjective,
        task: AgentTask,
        project_model: ProjectModel,
        files: list[ProjectFile],
        build_evidence: BuildEvidence | None = None,
        failure_feedback: list[str] | None = None,
    ) -> ChangeBundle | dict[str, object]: ...


__all__ = ["CodeEngineerPort"]
