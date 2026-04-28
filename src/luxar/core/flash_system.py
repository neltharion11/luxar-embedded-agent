from __future__ import annotations

from luxar.core.platform_adapter import PlatformAdapter
from luxar.models.schemas import FlashResult


class FlashSystem:
    def __init__(self, adapter: PlatformAdapter):
        self.adapter = adapter

    def flash_project(self, project_path: str, probe: str | None = None) -> FlashResult:
        return self.adapter.flash(project_path, probe=probe)



