"""Port for planning the first requirement-driven source-code change."""

from __future__ import annotations

from typing import Protocol

from luxar.domain.idf_examples import EspIdfExampleReference
from luxar.domain.project_analysis import ProjectAnalysis
from luxar.domain.repairs import ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


class FirmwareEditor(Protocol):
    def create_change(
        self,
        requirement: FirmwareRequirement,
        project_analysis: ProjectAnalysis,
        files: list[ProjectFile],
        reference_examples: list[EspIdfExampleReference],
        reference_files: list[ProjectFile],
    ) -> RepairPlan: ...
