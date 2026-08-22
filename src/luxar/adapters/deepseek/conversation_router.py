"""Model-backed selection of the conversation entry mode."""

from __future__ import annotations

import json

from pydantic import ValidationError

from luxar.adapters.deepseek.client import DeepSeekJsonClient, JsonCompletionClient
from luxar.adapters.deepseek.settings import DeepSeekSettings
from luxar.domain.conversation import ConversationDecision
from luxar.ports.errors import CapabilityError


class DeepSeekConversationRouter:
    """Let the model classify every user message and answer direct modes."""

    def __init__(
        self,
        client: JsonCompletionClient | None = None,
        model: str | None = None,
    ) -> None:
        self._client = client
        self._model = model

    def route(
        self,
        message: str,
        history: list[dict[str, str]],
        knowledge_status: str | None = None,
        previous_run: dict[str, object] | None = None,
    ) -> ConversationDecision:
        normalized = message.strip()
        system_prompt = (
            "你是 LUXAR 的统一消息入口路由器。每条最新消息都必须由你结合"
            "最近对话和上一轮执行证据选择处理模式。只返回 JSON object，"
            "不要返回 Markdown。不要用是否出现 GPIO、构建、烧录等关键词"
            "机械分类，要判断用户此刻真正希望系统执行什么。"
            "\n模式规则："
            "\n1. project_inspection：用户要读取、检查或分析当前项目代码，"
            "但没有要求修改。response 为空。"
            "\n2. firmware_task：用户要求创建、修改、构建、烧录、监视或调试"
            "项目，即需要工作流实际操作。含蓄表达（例如‘把它跑起来’、"
            "‘接着处理刚才那个’）也要结合历史判断。response 为空。"
            "\n3. workflow_status：用户只是在询问上一轮是否构建、烧录、修改了"
            "什么或采用了哪些例程，并未要求现在执行动作。必须严格依据"
            "previous_completed_run 回答；没有证据就明确说无法确认。"
            "\n4. casual_chat：问候、闲聊、知识解释、关于 LUXAR 能力或设计的"
            "讨论，不需要读取项目或执行项目操作。直接自然回答。"
            "\n必须区分询问与命令：‘烧录了吗’是 workflow_status；"
            "‘现在烧录’是 firmware_task。不要把历史里的旧指令本身当作新请求，"
            "但要用历史消解‘它、继续、刚才那个’等指代。"
            "project_inspection 和 firmware_task 的 response 必须为空；"
            "workflow_status 和 casual_chat 的 response 必须是完整自然语言。"
            "【关于外部知识库】外部知识库是否启用、是否为空、包含哪些文档，"
            "你只能依据 user prompt 中「当前系统状态」提供的 facts 回答；"
            "绝不能凭猜测声称知识库里存在或不存在任何资料。"
            "如果 facts 没有提供答案，或者你无法确认，必须如实回答"
            "“无法确认”或“我不知道”，绝不能编造。"
            "输出严格使用："
            '{"intent":"casual_chat|workflow_status|project_inspection|firmware_task",'
            '"response":"..."}'
        )
        knowledge_fact = (
            "未提供（调用方没有传入外部知识库状态）。"
            if knowledge_status is None
            else knowledge_status
        )
        safe_history = [
            {
                "role": item.get("role", "")[:20],
                "content": item.get("content", "")[:2000],
            }
            for item in history[-12:]
            if item.get("role") in {"user", "assistant"}
        ]
        client = self._client
        model = self._model
        if client is None:
            settings = DeepSeekSettings()
            client = DeepSeekJsonClient(settings)
            model = model or settings.fast_model
            self._client = client
            self._model = model
        assert model is not None
        payload = client.complete_json(
            system_prompt=system_prompt,
            user_prompt=json.dumps(
                {
                    "history": safe_history,
                    "latest_message": normalized,
                    "current_system_status": {
                        "external_knowledge_base": knowledge_fact,
                    },
                    "previous_completed_run": _compact_previous_run(previous_run),
                },
                ensure_ascii=False,
            ),
            model=model,
        )
        try:
            return ConversationDecision.model_validate(payload)
        except ValidationError as error:
            raise CapabilityError(
                category="invalid_schema",
                message="DeepSeek conversation route was invalid",
                retryable=False,
            ) from error


def _compact_previous_run(
    result: dict[str, object] | None,
) -> dict[str, object] | None:
    """Expose decision evidence without sending verbose command output."""

    if result is None:
        return None
    compact: dict[str, object] = {
        "build_executed": isinstance(result.get("build_evidence"), dict),
        "flash_executed": isinstance(result.get("flash_evidence"), dict),
    }
    for key in (
        "task_text",
        "status",
        "changed_files",
        "reference_examples",
        "requirement",
    ):
        if key in result:
            compact[key] = result[key]
    for key in ("build_evidence", "flash_evidence"):
        evidence = result.get(key)
        if isinstance(evidence, dict):
            compact[key] = {
                field: evidence[field]
                for field in ("success", "return_code", "error_category")
                if field in evidence
            }
    return compact
