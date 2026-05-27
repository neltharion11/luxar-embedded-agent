from __future__ import annotations

from abc import ABC, abstractmethod

from luxar.models.schemas import BuildResult, FlashResult, MonitorResult, ProbeResult


class PlatformAdapter(ABC):
    @abstractmethod
    def check_project_config(self, project_path: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def build(self, project_path: str, clean: bool = False) -> BuildResult:
        raise NotImplementedError

    @abstractmethod
    def flash(self, project_path: str, probe: str | None = None) -> FlashResult:
        raise NotImplementedError

    @abstractmethod
    def monitor(self, project_path: str, **kwargs) -> MonitorResult:
        raise NotImplementedError

    @abstractmethod
    def probe(self, project_path: str, probe_type: str = "i2c") -> ProbeResult:
        raise NotImplementedError



