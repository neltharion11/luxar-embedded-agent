"""DeepSeek 需求解析 Adapter：把自然语言任务转换成经过验证的固件需求。"""

from __future__ import annotations

import json

from pydantic import ValidationError

from luxar.adapters.deepseek.client import JsonCompletionClient
from luxar.domain.requirements import FirmwareRequirement
from luxar.ports.errors import CapabilityError


class DeepSeekRequirementParser:
    def __init__(
        self,
        client: JsonCompletionClient,
        model: str,
    ) -> None:
        # Adapter 只依赖 JSON Client 合同，因此测试可以注入 Fake Client。
        self._client = client
        self._model = model

    def parse(
        self,
        task_text: str,
    ) -> FirmwareRequirement:
        # Pydantic 自动生成 JSON Schema，告诉模型字段名称、类型和允许值。
        requirement_schema = FirmwareRequirement.model_json_schema()

        system_prompt = (
            "你是 LUXAR 的固件需求解析器。"
            "只返回一个 JSON object，不要添加 Markdown 或解释文字。"
            "输出必须符合下面的 JSON Schema。"
            "不要猜测用户没有提供的信息。"
            "无法确定的文本字段使用空字符串，"
            "无法确定的 GPIO 使用 null，"
            "并把缺失字段名加入 missing_fields。"
            "\nJSON Schema:\n"
            + json.dumps(
                requirement_schema,
                ensure_ascii=False,
            )
        )

        # json.dumps 把用户输入作为 JSON 数据包装，避免手工拼接引号和换行。
        user_prompt = json.dumps(
            {
                "task_text": task_text,
            },
            ensure_ascii=False,
        )

        payload = self._client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self._model,
        )

        try:
            # JSON 合法不代表业务合法，这里再由 Pydantic 检查字段和类型。
            return FirmwareRequirement.model_validate(payload)
        except ValidationError as error:
            raise CapabilityError(
                category="invalid_schema",
                message=(
                    "DeepSeek requirement response did not match "
                    "FirmwareRequirement"
                ),
                retryable=False,
            ) from error