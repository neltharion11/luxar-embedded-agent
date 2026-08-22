"""Port for turning a bounded source snapshot into reusable project analysis."""

from __future__ import annotations

from typing import Protocol

from luxar.domain.project_analysis import ProjectAnalysis
from luxar.domain.repairs import ProjectFile


class ProjectAnalyzer(Protocol):
    def analyze(
        self,
        *,
        project_name: str,
        target_chip: str | None,
        fingerprint: str,
        files: list[ProjectFile],
    ) -> ProjectAnalysis: ...
