from __future__ import annotations

from luxar.core.platform_adapter import PlatformAdapter
from luxar.models.schemas import MonitorResult


class UartMonitor:
    def __init__(self, adapter: PlatformAdapter):
        self.adapter = adapter

    def monitor_project(self, project_path: str, **kwargs) -> MonitorResult:
        return self.adapter.monitor(project_path, **kwargs)



