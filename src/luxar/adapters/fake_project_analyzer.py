"""Deterministic project analyzer used by graph tests."""

from __future__ import annotations

from luxar.domain.project_analysis import ProjectAnalysis
from luxar.domain.repairs import ProjectFile


class FakeProjectAnalyzer:
    def __init__(self, analyses: list[ProjectAnalysis]) -> None:
        self._analyses = list(analyses)
        self.calls: list[dict[str, object]] = []

    def analyze(
        self,
        *,
        project_name: str,
        target_chip: str | None,
        fingerprint: str,
        files: list[ProjectFile],
        inspection_request: str | None = None,
    ) -> ProjectAnalysis:
        self.calls.append(
            {
                "project_name": project_name,
                "target_chip": target_chip,
                "fingerprint": fingerprint,
                "files": list(files),
                "inspection_request": inspection_request,
            }
        )
        analysis = self._analyses.pop(0)
        return analysis.model_copy(
            update={"fingerprint": fingerprint, "cache_hit": False}
        )
