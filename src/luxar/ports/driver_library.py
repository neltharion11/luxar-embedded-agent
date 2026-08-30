"""Port for the application-wide reusable driver package library."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from luxar.domain.drivers import (
    DriverCandidate,
    DriverManifest,
    DriverPackage,
    DriverPublishSpec,
    DriverVerification,
)


class DriverLibraryPort(Protocol):
    def search(
        self,
        *,
        query: str = "",
        hardware: str = "",
        protocol: str = "",
        target_chip: str = "",
        limit: int = 20,
    ) -> list[DriverCandidate]: ...

    def read(self, driver_id: str, version: str | None = None) -> DriverPackage: ...

    def publish(
        self,
        *,
        project_path: Path,
        project_key: str,
        spec: DriverPublishSpec,
        verification: DriverVerification,
    ) -> DriverManifest: ...


__all__ = ["DriverLibraryPort"]
