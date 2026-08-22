"""Port for finding and reading examples from the installed ESP-IDF SDK."""

from __future__ import annotations

from typing import Protocol

from luxar.domain.idf_examples import EspIdfExampleReference
from luxar.domain.repairs import ProjectFile
from luxar.domain.requirements import FirmwareRequirement


class EspIdfExampleLibrary(Protocol):
    def search(
        self,
        requirement: FirmwareRequirement,
        *,
        limit: int = 2,
    ) -> list[EspIdfExampleReference]: ...

    def read(
        self,
        reference: EspIdfExampleReference,
    ) -> list[ProjectFile]: ...
