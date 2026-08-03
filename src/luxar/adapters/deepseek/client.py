"""DeepSeek JSON 客户端合同：把提示词和模型名转换为 JSON object 字典。"""

from __future__ import annotations

from typing import Protocol


class JsonCompletionClient(Protocol):
    # 该通信合同只理解提示词和 JSON，不理解 FirmwareRequirement 等业务对象。
    def complete_json(
        self,
        # * 后的参数必须按名称传入，避免把两个长提示词或模型名的位置写反。
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> dict[str, object]:
        ...
