import json

import pytest

from luxar.adapters.deepseek.conversation_router import (
    DeepSeekConversationRouter,
)
from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.domain.conversation import ConversationDecision
from luxar.ports.errors import CapabilityError


def test_greeting_mode_is_selected_by_model() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "casual_chat", "response": "你好，我是 LUXAR。"}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route("你好！", [])

    assert decision.intent == "casual_chat"
    assert "LUXAR" in decision.response
    assert len(client.calls) == 1
    system_prompt = client.calls[0][0]
    assert "绝不能向用户自称‘路由器’" in system_prompt


def test_model_id_is_only_used_for_api_connection_not_agent_identity() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "casual_chat", "response": "当前配置使用 fast-model。"}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    router.route("你调用的什么模型", [])

    system_prompt, user_prompt, requested_model = client.calls[0]
    payload = json.loads(user_prompt)
    assert "assistant_runtime" not in payload
    agent = payload["current_system_status"]["agent"]
    assert agent["tool"] == "inspect_agent_status"
    assert agent["conversation_model"]["model"] == "fast-model"
    assert agent["conversation_model"]["context_window_tokens"] == 32_768
    assert agent["conversation_model"]["context_compaction_threshold"] == 0.95
    assert agent["pdf_reader"]["text_extraction"] == "PyMuPDF"
    assert agent["rag"]["project"]["available"] is False
    assert agent["embedding"]["mode"] == "local_hash"
    assert agent["tools"]["count"] == 17
    assert agent["workflow"]["node_count"] == 22
    assert agent["next_operation"]["recommendation"] == "route_latest_user_input"
    assert requested_model == "fast-model"
    assert "LUXAR 是对外身份" in system_prompt
    assert "状态阻塞规则" in system_prompt


def test_explicit_firmware_message_is_routed_by_model() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "firmware_task", "response": "ignored"}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route("请构建 ESP32 GPIO2 闪烁固件", [])

    assert decision == ConversationDecision(intent="firmware_task")
    assert len(client.calls) == 1


def test_bare_flash_command_bypasses_model_and_enters_firmware_workflow() -> None:
    client = FakeJsonCompletionClient([])
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route("烧录", [])

    assert decision == ConversationDecision(intent="firmware_task")
    assert client.calls == []


def test_flash_command_survives_a_clarifying_complaint_in_same_message() -> None:
    client = FakeJsonCompletionClient([])
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route("我要补充什么？我让你烧录", [])

    assert decision.intent == "firmware_task"
    assert client.calls == []


def test_blank_display_diagnosis_bypasses_model_and_enters_project_inspection() -> None:
    client = FakeJsonCompletionClient([])
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route("那为什么屏幕还是没亮", [])

    assert decision == ConversationDecision(intent="project_inspection")
    assert client.calls == []


def test_explicit_absolute_pdf_read_bypasses_model_and_enters_knowledge_workflow() -> None:
    client = FakeJsonCompletionClient([])
    router = DeepSeekConversationRouter(client, "fast-model")
    message = (
        '"D:\\download\\中景园电子1.3英寸OLED技术资料V3.0\\'
        '1.3寸横屏规格书.pdf" 那么读取这个PDF'
    )

    decision = router.route(
        message,
        [
            {"role": "user", "content": "前面在讨论 OLED 技术资料。"},
            {"role": "assistant", "content": "当前项目尚未实现 OLED。"},
        ],
    )

    assert decision == ConversationDecision(intent="knowledge_task")
    assert client.calls == []


def test_focused_knowledge_followup_can_be_answered_without_retrieval() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "intent": "knowledge_task",
                "response": "SDA 是数据线，SCL 是时钟线。",
                "response_plan": {
                    "operation": "direct_answer",
                    "context_required": True,
                    "scope": "focused",
                    "confidence": 0.96,
                    "ambiguity": 0.02,
                    "answer_budget": 240,
                },
            }
        ]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route(
        "SCL 和 SDA 是哪两个引脚？",
        [
            {"role": "user", "content": "刚才分析的是 OLED 模组。"},
            {"role": "assistant", "content": "接口使用 I2C。"},
        ],
    )

    assert decision.intent == "knowledge_task"
    assert decision.response == "SDA 是数据线，SCL 是时钟线。"
    assert decision.response_plan is not None
    assert decision.response_plan.operation == "direct_answer"


def test_route_compatibility_keeps_answer_when_plan_is_embellished() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "intent": "knowledge_task",
                "response": "SDA 是数据线，SCL 是时钟线。",
                "response_plan": {
                    "action": "direct",
                    "context_required": "true",
                    "confidence": "0.9",
                    "answer_budget": "240",
                },
                "reason": "这是一个聚焦追问",
            }
        ]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route("SCL 和 SDA 是哪两个引脚？", [])

    assert decision.response == "SDA 是数据线，SCL 是时钟线。"
    assert decision.response_plan is not None
    assert decision.response_plan.operation == "direct_answer"
    assert decision.response_plan.context_required is True
    assert decision.response_plan.answer_budget == 240


def test_project_inspection_mode_is_selected_by_model() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "project_inspection", "response": ""}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route("检查当前项目", [])

    assert decision == ConversationDecision(intent="project_inspection")
    assert len(client.calls) == 1


def test_router_does_not_make_pre_source_knowledge_retrieval_decision() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "intent": "project_inspection",
                "response": "",
                "response_plan": {
                    "operation": "workflow",
                    "scope": "focused",
                    "knowledge_retrieval": "retrieve",
                    "knowledge_reason": "需要用芯片手册核对引脚限制",
                },
            }
        ]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route("检查 GPIO34 为什么不能输出", [])

    assert decision.response_plan is not None
    assert not hasattr(decision.response_plan, "knowledge_retrieval")


def test_fuzzy_followup_can_enter_firmware_workflow_by_model_judgment() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "firmware_task", "response": ""}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route(
        "那就让它跑起来吧",
        [
            {"role": "user", "content": "实现 P13 输出低电平"},
            {"role": "assistant", "content": "代码已构建，但尚未烧录。"},
        ],
    )

    assert decision.intent == "firmware_task"
    payload = json.loads(client.calls[0][1])
    assert payload["latest_message"] == "那就让它跑起来吧"


def test_status_mode_receives_compact_previous_run_evidence() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "intent": "workflow_status",
                "response": "构建成功，但还没有烧录。",
            }
        ]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route(
        "板子上现在有了吗？",
        [],
        previous_run={
            "task_text": "P13 输出低电平",
            "changed_files": ["main/t2.c"],
            "build_evidence": {
                "success": True,
                "return_code": 0,
                "stdout": "very long output",
            },
        },
    )

    assert decision.intent == "workflow_status"
    payload = json.loads(client.calls[0][1])
    previous = payload["previous_completed_run"]
    assert previous["build_executed"] is True
    assert previous["flash_executed"] is False
    assert previous["build_evidence"] == {"success": True, "return_code": 0}
    assert "stdout" not in previous["build_evidence"]


def test_status_mode_receives_bounded_build_failure_diagnostics() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "workflow_status", "response": "已定位到编译错误。"}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    router.route(
        "分析错误原因",
        [],
        previous_run={
            "status": "blocked",
            "build_evidence": {
                "success": False,
                "return_code": 2,
                "error_category": "source",
                "stdout_summary": "very long stdout",
                "stderr_summary": "components/ssd1306/ssd1306.h:6: fatal error: driver/gpio.h: No such file or directory",
                "diagnostics": [
                    {
                        "file": "components/ssd1306/ssd1306.h",
                        "line": 6,
                        "column": 10,
                        "severity": "error",
                        "code": None,
                        "message": "driver/gpio.h: No such file or directory",
                    }
                ],
            },
        },
    )

    payload = json.loads(client.calls[0][1])
    evidence = payload["previous_completed_run"]["build_evidence"]
    assert evidence["stderr_summary"].startswith(
        "components/ssd1306/ssd1306.h:6"
    )
    assert evidence["diagnostics"] == [
        {
            "file": "components/ssd1306/ssd1306.h",
            "line": 6,
            "column": 10,
            "severity": "error",
            "code": None,
            "message": "driver/gpio.h: No such file or directory",
        }
    ]
    assert "stdout_summary" not in evidence


def test_status_mode_receives_pdf_completion_evidence_without_preview() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "workflow_status", "response": "PDF 已读取完成，共 37 页。"}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    router.route(
        "PDF 读完了吗？",
        [],
        previous_run={
            "status": "completed",
            "knowledge_result": {
                "read_pdf": True,
                "title": "OLED 规格书",
                "total_pages": 37,
                "batches": 4,
                "characters": 121249,
                "preview": "very long extracted text",
                "technical_context": "协议：I2C；地址：0x3C。",
            },
        },
    )

    payload = json.loads(client.calls[0][1])
    knowledge = payload["previous_completed_run"]["knowledge_result"]
    assert knowledge == {
        "read_pdf": True,
        "title": "OLED 规格书",
        "total_pages": 37,
        "batches": 4,
        "characters": 121249,
        "technical_context": "协议：I2C；地址：0x3C。",
    }
    assert "preview" not in knowledge


def test_ambiguous_message_uses_full_history_below_model_window() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "casual_chat", "response": "我可以解释这个项目。"}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")
    history = [
        {"role": "user", "content": f"old-{index}"}
        for index in range(15)
    ]

    decision = router.route("你能做什么", history)

    assert decision.response == "我可以解释这个项目。"
    _, user_prompt, model = client.calls[0]
    payload = json.loads(user_prompt)
    assert model == "fast-model"
    assert len(payload["history"]) == 15
    assert payload["history"][0]["content"] == "old-0"
    assert payload["latest_message"] == "你能做什么"


def test_history_is_compacted_at_95_percent_of_model_window() -> None:
    client = FakeJsonCompletionClient(
        [{"summary": "目标是设置 P32 高电平；上次构建失败，尚未烧录。"}]
    )
    router = DeepSeekConversationRouter(
        client,
        "custom-small-model",
        context_window_tokens=4096,
    )
    history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": (f"第 {index} 条消息：" + "项目证据" * 500),
        }
        for index in range(8)
    ]

    prepared = router.prepare_history(
        "重试",
        history,
        previous_run={
            "status": "blocked",
            "task_text": "设置 P32 高电平并烧录",
            "last_error": "ESP-IDF 构建失败",
        },
    )

    assert prepared.compacted is True
    assert prepared.context_window_tokens == 4096
    assert prepared.covered_message_count > 0
    assert prepared.summary.startswith("目标是设置 P32")
    assert prepared.history[0]["content"].startswith(
        "【LUXAR 压缩的早期对话上下文】"
    )
    summary_prompt = json.loads(client.calls[0][1])
    assert summary_prompt["latest_run"]["status"] == "blocked"
    assert summary_prompt["latest_run"]["last_error"] == "ESP-IDF 构建失败"


def test_invalid_model_route_is_mapped_to_capability_error() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "casual_chat", "response": "", "extra": True}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    with pytest.raises(CapabilityError, match="route was invalid"):
        router.route("介绍一下自己", [])
