from __future__ import annotations

from luxar.core.platform_adapter import PlatformAdapter
from luxar.models.schemas import BuildResult


class BuildSystem:
    def __init__(self, adapter: PlatformAdapter):
        self.adapter = adapter

    def build_project(self, project_path: str, clean: bool = False) -> BuildResult:
        return self.adapter.build(project_path, clean=clean)



