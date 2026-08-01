from __future__ import annotations

from pathlib import Path
from typing import Sequence

from luxar.domain.repairs import ProjectFile, RepairPlan


class FakeWorkspace:
    def __init__(
        self,
        files: Sequence[ProjectFile],
    ) -> None:
        self.files = list(files)
        self.read_calls: list[Path] = []
        self.apply_calls: list[tuple[Path, RepairPlan]] = []

    def read_project_files(
        self,
        project_path: Path,
    ) -> list[ProjectFile]:
        self.read_calls.append(project_path)

        return list(self.files)

    def apply_repair(
        self,
        project_path: Path,
        repair: RepairPlan,
    ) -> list[str]:
        self.apply_calls.append((project_path, repair))

        return [
            replacement.path
            for replacement in repair.replacements
        ]