"""DeepSeek 计划 Adapter：把经过验证的固件需求转换成有序执行计划。"""

from __future__ import annotations

import json

from pydantic import ValidationError

from luxar.adapters.deepseek.client import JsonCompletionClient
from luxar.domain.plans import ExecutionPlan
from luxar.domain.requirements import FirmwareRequirement
from luxar.ports.errors import CapabilityError


class DeepSeekPlanner:
    def __init__(
        self,
        client: JsonCompletionClient,
        model: str,
    ) -> None:
        # Client 负责通信；Planner 负责计划 Prompt 和领域验证。
        self._client = client
        self._model = model

    def create_plan(
        self,
        requirement: FirmwareRequirement,
    ) -> ExecutionPlan:
        plan_schema = ExecutionPlan.model_json_schema()

        system_prompt = (
            "你是 LUXAR 的 ESP-IDF 固件执行计划生成器。"
            "只返回一个 JSON object，不要添加 Markdown 或解释文字。"
            "输出必须符合下面的 JSON Schema。"
            "只能使用 Schema 允许的步骤类型，不能发明新动作。"
            "步骤必须按照实际执行顺序排列。"
            "\nJSON Schema:\n"
            + json.dumps(
                plan_schema,
                ensure_ascii=False,
            )
        )

        # requirement 已经是 Pydantic 对象，先转换成适合 JSON 的普通数据。
        requirement_data = requirement.model_dump(
            mode="json",
        )

        user_prompt = json.dumps(
            {
                "requirement": requirement_data,
            },
            ensure_ascii=False,
        )

        payload = self._client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self._model,
        )

        try:
            return ExecutionPlan.model_validate(payload)
        except ValidationError as error:
            raise CapabilityError(
                category="invalid_schema",
                message=(
                    "DeepSeek planning response did not match "
                    "ExecutionPlan"
                ),
                retryable=False,
            ) from error