"""需求解析 Fake：测试时返回预设需求，并记录收到的自然语言任务。"""

from __future__ import annotations

from luxar.domain.requirements import FirmwareRequirement


class FakeRequirementParser:
    def __init__(self, requirement: FirmwareRequirement) -> None:
        self.requirement = requirement
        # calls 让测试验证节点是否真的调用了该能力，以及传入了什么参数。
        self.calls: list[str] = []

    def parse(self, task_text: str) -> FirmwareRequirement:
        self.calls.append(task_text)
        return self.requirement
