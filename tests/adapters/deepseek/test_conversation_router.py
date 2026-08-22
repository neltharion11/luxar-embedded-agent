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


def test_explicit_firmware_message_is_routed_by_model() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "firmware_task", "response": "ignored"}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route("请构建 ESP32 GPIO2 闪烁固件", [])

    assert decision == ConversationDecision(intent="firmware_task")
    assert len(client.calls) == 1


def test_project_inspection_mode_is_selected_by_model() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "project_inspection", "response": ""}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    decision = router.route("检查当前项目", [])

    assert decision == ConversationDecision(intent="project_inspection")
    assert len(client.calls) == 1


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


def test_ambiguous_message_uses_model_with_bounded_history() -> None:
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
    assert len(payload["history"]) == 12
    assert payload["history"][0]["content"] == "old-3"
    assert payload["latest_message"] == "你能做什么"


def test_invalid_model_route_is_mapped_to_capability_error() -> None:
    client = FakeJsonCompletionClient(
        [{"intent": "casual_chat", "response": "", "extra": True}]
    )
    router = DeepSeekConversationRouter(client, "fast-model")

    with pytest.raises(CapabilityError, match="route was invalid"):
        router.route("介绍一下自己", [])
