from __future__ import annotations

import json

import pytest

from luxar.adapters.deepseek.continuous_agent_step import (
    DeepSeekContinuousAgentStep,
)
from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.domain.continuous_agent.events import ConversationEvent
from luxar.domain.continuous_agent.steps import (
    AgentStepContext,
    AgentToolDescriptor,
    AskUser,
    AssistantReply,
    DomainWorkflowCall,
    ToolCallBatch,
)
from luxar.ports.errors import CapabilityError


def _context() -> AgentStepContext:
    return AgentStepContext(
        session_id="session-1",
        turn_id="turn-2",
        project_key="0:test4",
        recent_events=[
            ConversationEvent(
                event_id="turn-1:user",
                turn_id="turn-1",
                kind="user_message",
                sequence=1,
                payload={"content": "重新烧录"},
            ),
            ConversationEvent(
                event_id="turn-1:assistant",
                turn_id="turn-1",
                kind="assistant_message",
                sequence=2,
                payload={"content": "还缺少串口"},
            ),
            ConversationEvent(
                event_id="turn-2:user",
                turn_id="turn-2",
                kind="user_message",
                sequence=3,
                payload={"content": "串口好了，是 COM4"},
            ),
        ],
        tools=[
            AgentToolDescriptor(
                name="device.flash",
                description="构建后烧录固件",
                input_schema={"type": "object"},
                read_only=False,
                requires_approval=True,
            )
        ],
    )


def test_step_adapter_selects_tool_from_fuzzy_followup() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "step": {
                    "type": "tool_calls",
                    "calls": [
                        {
                            "call_id": "call-flash",
                            "tool_name": "device.flash",
                            "arguments": {"serial_port": "COM4"},
                        }
                    ],
                }
            }
        ]
    )
    adapter = DeepSeekContinuousAgentStep(client, "fast-model")

    step = adapter.decide_next_step(_context())

    assert isinstance(step, ToolCallBatch)
    assert step.calls[0].arguments == {"serial_port": "COM4"}
    system_prompt, user_prompt, model = client.calls[0]
    assert "不要依赖‘继续/重试’等固定关键词" in system_prompt
    assert "不能通过工具发现" in system_prompt
    assert json.loads(user_prompt)["recent_events"][-1]["payload"] == {
        "content": "串口好了，是 COM4"
    }
    assert model == "fast-model"


def test_step_adapter_can_reply_without_starting_workflow() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "step": {
                    "type": "assistant_reply",
                    "content": "构建已通过，但尚未烧录。",
                }
            }
        ]
    )

    step = DeepSeekContinuousAgentStep(client, "fast-model").decide_next_step(
        _context()
    )

    assert isinstance(step, AssistantReply)


def test_reply_streamer_requests_natural_fact_grounded_chinese() -> None:
    class StreamingClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        def stream_text(self, **kwargs: str) -> object:
            self.calls.append(kwargs)
            yield "已经修改显示偏移，"
            yield "并通过构建验证。"

    client = StreamingClient()
    adapter = DeepSeekContinuousAgentStep(client, "fast-model")  # type: ignore[arg-type]

    chunks = list(adapter.stream_reply(draft="内部摘要", context=_context()))

    assert chunks == ["已经修改显示偏移，", "并通过构建验证。"]
    system_prompt = client.calls[0]["system_prompt"]
    assert "流利、自然、简洁但具体" in system_prompt
    assert "固定六段模板" in system_prompt
    payload = json.loads(client.calls[0]["user_prompt"])
    assert payload["draft"] == "内部摘要"
    assert payload["context"]["project_key"] == "0:test4"


def test_step_adapter_can_delegate_complex_change_to_domain_workflow() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "step": {
                    "type": "domain_workflow",
                    "call_id": "change-1",
                    "workflow_name": "project.change",
                    "task": "修改多个组件并完成非回归验收",
                }
            }
        ]
    )
    context = _context().model_copy(
        update={
            "domain_workflows": [
                {
                    "name": "project.change",
                    "description": "复杂工程变更",
                }
            ]
        }
    )

    step = DeepSeekContinuousAgentStep(
        client,
        "fast-model",
    ).decide_next_step(context)

    assert isinstance(step, DomainWorkflowCall)
    assert step.workflow_name == "project.change"
    assert "domain_workflow" in client.calls[0][0]


def test_step_adapter_compacts_events_into_self_contained_summary() -> None:
    client = FakeJsonCompletionClient([{"summary": "目标是烧录 COM4，并保留 TWAI。"}])
    adapter = DeepSeekContinuousAgentStep(client, "fast-model")

    summary = adapter.compact_context(
        previous_summary="旧目标",
        events=_context().recent_events,
    )

    assert summary == "目标是烧录 COM4，并保留 TWAI。"
    payload = json.loads(client.calls[0][1])
    assert payload["previous_summary"] == "旧目标"
    assert len(payload["events"]) == 3


def test_step_adapter_uses_typed_missing_input_request() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "step": {
                    "type": "ask_user",
                    "request": {
                        "kind": "missing_input",
                        "request_id": "missing-board",
                        "prompt": "请选择要操作的开发板",
                        "fields": ["board_id"],
                        "reason": "发现两块开发板，无法安全自动选择",
                    },
                }
            }
        ]
    )

    step = DeepSeekContinuousAgentStep(client, "fast-model").decide_next_step(
        _context()
    )

    assert isinstance(step, AskUser)
    assert step.request.fields == ["board_id"]


def test_step_adapter_repairs_schema_once() -> None:
    client = FakeJsonCompletionClient(
        [
            {"action": "reply", "content": "旧格式"},
            {
                "step": {
                    "type": "assistant_reply",
                    "content": "已修复格式。",
                }
            },
        ]
    )

    step = DeepSeekContinuousAgentStep(client, "fast-model").decide_next_step(
        _context()
    )

    assert isinstance(step, AssistantReply)
    assert len(client.calls) == 2


def test_step_adapter_invalid_schema_is_model_error_not_user_question() -> None:
    client = FakeJsonCompletionClient([{"bad": True}, {"still_bad": True}])

    with pytest.raises(CapabilityError) as captured:
        DeepSeekContinuousAgentStep(client, "fast-model").decide_next_step(
            _context()
        )

    assert captured.value.category == "invalid_schema"
    assert "ask_user" not in captured.value.message
