from __future__ import annotations

from pathlib import Path
from typing import Protocol

from luxar.domain.repairs import ProjectFile, RepairPlan


class WorkspacePort(Protocol):
    def read_project_files(
        self,
        project_path: Path,
    ) -> list[ProjectFile]:
        ...

    def apply_repair(
        self,
        project_path: Path,
        repair: RepairPlan,
    ) -> list[str]:
        ...