from __future__ import annotations

from luxar.domain.requirements import FirmwareRequirement


class FakeRequirementParser:
    def __init__(self, requirement: FirmwareRequirement) -> None:
        self.requirement = requirement
        self.calls: list[str] = []

    def parse(self, task_text: str) -> FirmwareRequirement:
        self.calls.append(task_text)
        return self.requirement