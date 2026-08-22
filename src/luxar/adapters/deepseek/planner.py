"""DeepSeek 计划 Adapter：把经过验证的固件需求转换成有序执行计划。"""

from __future__ import annotations

import json

from pydantic import ValidationError

from luxar.adapters.deepseek.client import JsonCompletionClient
from luxar.domain.plans import ExecutionPlan
from luxar.domain.requirements import FirmwareRequirement
from luxar.domain.project_analysis import ProjectAnalysis
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
        project_analysis: ProjectAnalysis | None = None,
    ) -> ExecutionPlan:
        plan_schema = ExecutionPlan.model_json_schema()

        system_prompt = (
            "你是 LUXAR 的 ESP-IDF 固件执行计划生成器。"
            "只返回一个 JSON object，不要添加 Markdown 或解释文字。"
            "输出必须符合下面的 JSON Schema。"
            "只能使用 Schema 允许的步骤类型，不能发明新动作。"
            "步骤必须按照实际执行顺序排列。"
            "项目现状已经由 Python 强制分析；规划必须以 project_analysis 为依据。"
            "项目是否存在已经由 Graph 处理，禁止生成 create_project。"
            "如果用户目标尚未在当前源码中实现，计划必须先包含 implement_change，"
            "再包含 build_project。不得用构建旧代码代替实现用户需求。"
            "如果分析证明目标已经实现，可以省略 implement_change。"
            "不得添加 requirement.peripherals 中不存在的外设。"
            "当 project_type 为 empty 时，只生成最小 ESP-IDF 项目框架"
            "及必要的构建步骤，不得擅自加入 GPIO 或示例业务功能。"
            "requirement.constraints 中的 workflow_action 是用户明确要求的执行动作："
            "workflow_action:build 必须包含 build_project；workflow_action:flash 必须"
            "包含 build_project、flash_project；workflow_action:monitor 必须包含"
            "build_project、flash_project、monitor_project。若当前代码已经满足继承的"
            "功能目标，不得为了继续构建或烧录而重复 implement_change。"
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

        request: dict[str, object] = {"requirement": requirement_data}
        if project_analysis is not None:
            request["project_analysis"] = project_analysis.model_dump(mode="json")
        user_prompt = json.dumps(request, ensure_ascii=False)

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
