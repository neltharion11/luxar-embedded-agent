"""Deterministic firmware editor used by graph tests."""

from __future__ import annotations

from luxar.domain.idf_examples import EspIdfExampleReference
from luxar.domain.project_analysis import ProjectAnalysis
from luxar.domain.repairs import ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


class FakeFirmwareEditor:
    def __init__(self, change: RepairPlan) -> None:
        self.change = change
        self.calls: list[
            tuple[
                FirmwareRequirement,
                ProjectAnalysis,
                list[ProjectFile],
                list[EspIdfExampleReference],
                list[ProjectFile],
            ]
        ] = []

    def create_change(
        self,
        requirement: FirmwareRequirement,
        project_analysis: ProjectAnalysis,
        files: list[ProjectFile],
        reference_examples: list[EspIdfExampleReference],
        reference_files: list[ProjectFile],
    ) -> RepairPlan:
        self.calls.append(
            (
                requirement,
                project_analysis,
                list(files),
                list(reference_examples),
                list(reference_files),
            )
        )
        return self.change
