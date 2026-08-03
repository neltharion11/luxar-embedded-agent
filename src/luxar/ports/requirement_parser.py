"""需求解析 Port：规定“自然语言转固件需求”能力的最小接口。"""

from __future__ import annotations

from typing import Protocol

from luxar.domain.requirements import FirmwareRequirement


class RequirementParser(Protocol):
    # Protocol 使用结构化类型：实现类只需提供同签名方法，不必显式继承本类。
    def parse(self, task_text: str) -> FirmwareRequirement:
        ...


