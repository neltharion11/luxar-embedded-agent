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


def test_step_adapter_streams_natural_commentary_before_decision_finishes() -> None:
    class StreamingJsonClient:
        def stream_json_text(self, **kwargs: str) -> object:
            del kwargs
            yield '{"commentary":"我先核对 OLED 驱动里的寻址方式，'
            yield '再对照当前屏幕现象定位偏移。","step":{"type":"tool_calls","calls":['
            yield '{"call_id":"read-oled","tool_name":"device.flash","arguments":{}}]}}'

    updates: list[str] = []
    adapter = DeepSeekContinuousAgentStep(
        StreamingJsonClient(),  # type: ignore[arg-type]
        "fast-model",
    )

    step = adapter.decide_next_step_streaming(
        _context(),
        on_commentary=updates.append,
    )

    assert isinstance(step, ToolCallBatch)
    assert "".join(updates) == (
        "我先核对 OLED 驱动里的寻址方式，再对照当前屏幕现象定位偏移。"
    )
    assert all("commentary" not in item and "tool_calls" not in item for item in updates)
    prompt = adapter._system_prompt()
    assert "commentary 必须是 JSON 的第一个字段" in prompt
    assert "不要输出标题、字段名、流水账" in prompt


def test_step_adapter_recovers_when_streamed_json_is_truncated() -> None:
    class TruncatedStreamingJsonClient:
        def __init__(self) -> None:
            self.fallback_calls = 0

        def stream_json_text(self, **kwargs: str) -> object:
            del kwargs
            yield '{"commentary":"我先确认当前工程状态，'
            yield '然后继续执行","step":{"type":"tool_calls"'

        def complete_json(self, **kwargs: str) -> dict[str, object]:
            del kwargs
            self.fallback_calls += 1
            return {
                "commentary": "我先确认当前工程状态，然后继续执行",
                "step": {
                    "type": "assistant_reply",
                    "content": "已确认当前工程状态。",
                },
            }

    client = TruncatedStreamingJsonClient()
    updates: list[str] = []
    step = DeepSeekContinuousAgentStep(client, "fast-model").decide_next_step_streaming(
        _context(),
        on_commentary=updates.append,
    )

    assert isinstance(step, AssistantReply)
    assert client.fallback_calls == 1
    assert "我先确认当前工程状态" in "".join(updates)


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


def test_step_adapter_system_prompt_forbids_embedding_source_code() -> None:
    """A：决策提示词必须禁止在 JSON 里内嵌完整源码（长 JSON + 转义是
    invalid_json/截断的主要来源），引导走结构化工具路径。"""
    prompt = DeepSeekContinuousAgentStep(
        FakeJsonCompletionClient([]), "fast-model"
    )._system_prompt()
    assert "禁止在 JSON 字符串字段里内嵌完整源码" in prompt
    assert "font.export / workspace.apply_change_bundle" in prompt
    assert "domain_workflow 时 task 只写意图" in prompt


def test_step_adapter_passes_repair_and_max_tokens_to_decisions() -> None:
    """B/C：决策调用必须开启 JSON 修复（repair=True）并带输出上限，
    使长任务截断/损坏的 JSON 有机会被修复而非直接终态失败。"""
    client = FakeJsonCompletionClient(
        [
            {
                "step": {
                    "type": "tool_calls",
                    "calls": [
                        {
                            "call_id": "c1",
                            "tool_name": "device.flash",
                            "arguments": {"serial_port": "COM4"},
                        }
                    ],
                }
            }
        ]
    )
    adapter = DeepSeekContinuousAgentStep(client, "fast-model")

    adapter.decide_next_step(_context())

    assert client.options == [
        {"repair": True, "max_tokens": 8192}
    ]


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
    assert captured.value.details["initial_validation_errors"]
    assert captured.value.details["repair_validation_errors"]
    assert captured.value.details["initial_payload_keys"] == ["bad"]


def test_system_prompt_mandates_font_bitmap_tools() -> None:
    """决策提示必须强制使用取模工具，避免模型手写字体位图。"""
    prompt = DeepSeekContinuousAgentStep._system_prompt()
    assert "font.extract" in prompt
    assert "font.export" in prompt
    assert "禁止手写" in prompt
    assert "字模" in prompt
    assert "SSD1306" in prompt
    assert "SH1106" in prompt
    assert "ask_user 询问字符集" in prompt
    assert "已经检索知识库" in prompt
    assert "最相关资料标题" in prompt


def test_font_tools_are_injected_into_model_context() -> None:
    """取模工具必须出现在模型可决策的工具目录中（注入链路无缺失）。"""
    from luxar.adapters.continuous_agent_tools import create_core_tool_registry

    registry = create_core_tool_registry()
    names = {descriptor.name for descriptor in registry.descriptors()}
    assert "font.extract" in names
    assert "font.export" in names
    assert any(
        descriptor.name == "font.export"
        and descriptor.requires_approval is True
        and descriptor.read_only is False
        for descriptor in registry.descriptors()
    )
    assert any(
        descriptor.name == "font.extract"
        and descriptor.read_only is True
        for descriptor in registry.descriptors()
    )
