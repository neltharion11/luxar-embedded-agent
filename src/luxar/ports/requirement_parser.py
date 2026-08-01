from __future__ import annotations

from typing import Protocol

from luxar.domain.requirements import FirmwareRequirement


class RequirementParser(Protocol):
    def parse(self, task_text: str) -> FirmwareRequirement:
        ...


