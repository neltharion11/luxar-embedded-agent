from __future__ import annotations

from luxar.core.platform_adapter import PlatformAdapter
from luxar.models.schemas import ProbeResult


class ProbeSystem:
    def __init__(self, adapter: PlatformAdapter):
        self.adapter = adapter

    def probe_project(self, project_path: str, probe_type: str = "i2c") -> ProbeResult:
        return self.adapter.probe(project_path, probe_type=probe_type)
