"""DeepSeek JSON 客户端 Fake：按顺序返回预设字典，并记录每次提示词和模型。"""

from __future__ import annotations

from collections.abc import Sequence


class FakeJsonCompletionClient:
    def __init__(
        self,
        responses: Sequence[dict[str, object]],
    ) -> None:
        # 复制输入序列，使内部响应队列不受调用方后续修改影响。
        self.responses = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> dict[str, object]:
        # 先记录调用；即使响应耗尽，测试仍能看到出错前传入了什么。
        self.calls.append(
            (
                system_prompt,
                user_prompt,
                model,
            )
        )

        # Fake 不访问网络，也不能在未配置时替测试编造模型结果。
        if not self.responses:
            raise RuntimeError(
                "no configured JSON completion response remaining"
            )

        # pop(0) 按配置先后取值；dict(response) 返回副本，隔离外部修改。
        response = self.responses.pop(0)

        return dict(response)
