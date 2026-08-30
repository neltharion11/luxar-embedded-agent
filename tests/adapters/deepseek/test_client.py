from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

import luxar.adapters.deepseek.client as client_module
from luxar.adapters.deepseek.client import DeepSeekJsonClient
from luxar.adapters.deepseek.settings import DeepSeekSettings
from luxar.model_config import ModelEndpoint
from luxar.ports.errors import CapabilityError, CapabilityErrorCategory


class StubCompletions:
    def __init__(
        self,
        *,
        response: object | None = None,
        error: Exception | None = None,
        sequence: list[object] | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.sequence = list(sequence) if sequence else []
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        if self.sequence:
            return self.sequence.pop(0)

        return self.response


class StubSdkClient:
    def __init__(self, completions: StubCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def make_response(
    content: str | None,
    finish_reason: str | None = None,
) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


def make_stream(*contents: str | None) -> list[object]:
    return [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
        )
        for content in contents
    ]


def make_client(completions: StubCompletions) -> DeepSeekJsonClient:
    settings = DeepSeekSettings(api_key="test-key")
    return DeepSeekJsonClient(
        settings,
        sdk_client=StubSdkClient(completions),  # type: ignore[arg-type]
    )


def test_client_sends_json_mode_request_and_returns_object() -> None:
    completions = StubCompletions(
        response=make_response('{"target": "esp32", "gpio": 2}')
    )
    client = make_client(completions)

    result = client.complete_json(
        system_prompt="Return one JSON object",
        user_prompt="Create an ESP32 GPIO 2 requirement",
        model="deepseek-v4-flash",
    )

    assert result == {"target": "esp32", "gpio": 2}
    assert completions.calls == [
        {
            "model": "deepseek-v4-flash",
            "messages": [
                {
                    "role": "system",
                    "content": "Return one JSON object",
                },
                {
                    "role": "user",
                    "content": "Create an ESP32 GPIO 2 requirement",
                },
            ],
            "response_format": {"type": "json_object"},
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    ]


def test_client_yields_provider_stream_deltas_without_fake_chunking() -> None:
    completions = StubCompletions(response=make_stream("修改已", None, "完成。"))
    client = make_client(completions)

    chunks = list(
        client.stream_text(
            system_prompt="自然回答",
            user_prompt="总结事实",
            model="deepseek-v4-flash",
        )
    )

    assert chunks == ["修改已", "完成。"]
    assert completions.calls == [
        {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": "自然回答"},
                {"role": "user", "content": "总结事实"},
            ],
            "stream": True,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    ]


def test_client_streams_json_mode_without_buffering_the_response() -> None:
    completions = StubCompletions(
        response=make_stream('{"commentary":"我先检查', '工程。","step":{}}')
    )
    client = make_client(completions)

    chunks = list(
        client.stream_json_text(
            system_prompt="输出决策 JSON",
            user_prompt="检查 OLED 工程",
            model="deepseek-v4-flash",
        )
    )

    assert chunks == ['{"commentary":"我先检查', '工程。","step":{}}']
    assert completions.calls[0]["stream"] is True
    assert completions.calls[0]["response_format"] == {"type": "json_object"}
    assert completions.calls[0]["extra_body"] == {
        "thinking": {"type": "disabled"}
    }


def test_client_honors_explicit_deepseek_thinking_configuration() -> None:
    completions = StubCompletions(response=make_stream("自然更新"))
    client = DeepSeekJsonClient(
        ModelEndpoint(
            provider="deepseek",
            api_key="test-key",
            model="deepseek-v4-pro",
            thinking_enabled=True,
            thinking_effort="max",
        ),
        sdk_client=StubSdkClient(completions),  # type: ignore[arg-type]
    )

    assert list(
        client.stream_text(
            system_prompt="自然回复",
            user_prompt="检查工程",
            model="deepseek-v4-pro",
        )
    ) == ["自然更新"]
    assert completions.calls[0]["extra_body"] == {
        "thinking": {"type": "enabled"}
    }
    assert completions.calls[0]["reasoning_effort"] == "max"


def test_client_rejects_empty_provider_stream() -> None:
    client = make_client(StubCompletions(response=make_stream(None, "")))

    with pytest.raises(CapabilityError) as captured:
        list(
            client.stream_text(
                system_prompt="system",
                user_prompt="user",
                model="deepseek-v4-flash",
            )
        )

    assert captured.value.category == "empty_response"


def test_client_builds_sdk_with_deepseek_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    stub_sdk = StubSdkClient(StubCompletions(response=make_response("{}")))

    def fake_openai(**kwargs: object) -> StubSdkClient:
        captured.update(kwargs)
        return stub_sdk

    monkeypatch.setattr(client_module, "OpenAI", fake_openai)
    settings = DeepSeekSettings(
        api_key="constructor-secret",
        base_url="https://api.deepseek.com",
        timeout_seconds=15,
    )

    DeepSeekJsonClient(settings)

    assert captured == {
        "api_key": "constructor-secret",
        "base_url": "https://api.deepseek.com",
        "timeout": 15.0,
        "max_retries": 0,
    }


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(choices=[]),
        make_response(None),
        make_response("   "),
    ],
)
def test_client_rejects_empty_response(response: object) -> None:
    client = make_client(StubCompletions(response=response))

    with pytest.raises(CapabilityError) as captured:
        client.complete_json(
            system_prompt="system",
            user_prompt="user",
            model="deepseek-v4-flash",
        )

    assert captured.value.category == "empty_response"
    assert captured.value.retryable is True


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        '["valid JSON", "but not an object"]',
    ],
)
def test_client_rejects_invalid_json_object(content: str) -> None:
    client = make_client(StubCompletions(response=make_response(content)))

    with pytest.raises(CapabilityError) as captured:
        client.complete_json(
            system_prompt="system",
            user_prompt="user",
            model="deepseek-v4-flash",
        )

    assert captured.value.category == "invalid_json"
    assert captured.value.retryable is True


def test_client_reports_truncation_when_finish_reason_is_length() -> None:
    """C：输出达到 max_tokens 被截断时，报 truncated 而非笼统 invalid_json。"""
    client = make_client(
        StubCompletions(
            response=make_response('{"step": {"type": "tool_calls"', "length")
        )
    )

    with pytest.raises(CapabilityError) as captured:
        client.complete_json(
            system_prompt="system",
            user_prompt="user",
            model="deepseek-v4-flash",
        )

    assert captured.value.category == "truncated"
    assert captured.value.retryable is False
    assert captured.value.details["finish_reason"] == "length"


def test_client_repairs_broken_json_once_when_repair_enabled() -> None:
    """B：JSON 语法损坏时，把原文+错误位置回给模型修复一次，成功即返回。"""
    completions = StubCompletions(
        sequence=[
            make_response('{"step": {"type": "tool_calls", "calls": ['),
            make_response(
                '{"step": {"type": "tool_calls", "calls": []}}'
            ),
        ]
    )
    client = make_client(completions)

    result = client.complete_json(
        system_prompt="system",
        user_prompt="user",
        model="deepseek-v4-flash",
        repair=True,
    )

    assert result == {"step": {"type": "tool_calls", "calls": []}}
    assert len(completions.calls) == 2
    repair_prompt = str(completions.calls[1]["messages"][0]["content"])
    assert "修复下面 JSON 的语法错误" in repair_prompt
    assert "broken_json" in str(completions.calls[1]["messages"][1]["content"])


def test_client_invalid_json_survives_when_repair_also_fails() -> None:
    """B：修复也失败时，仍抛 invalid_json（含原始响应细节）。"""
    completions = StubCompletions(
        sequence=[
            make_response('{"step": {"type":'),
            make_response("still-not-json"),
        ]
    )
    client = make_client(completions)

    with pytest.raises(CapabilityError) as captured:
        client.complete_json(
            system_prompt="system",
            user_prompt="user",
            model="deepseek-v4-flash",
            repair=True,
        )

    assert captured.value.category == "invalid_json"
    # 保留原始（首次损坏）响应的细节，便于定位
    assert captured.value.details["response_length"] == len('{"step": {"type":')
    assert len(completions.calls) == 2


def test_client_passes_max_tokens_to_provider_request() -> None:
    """C：显式 max_tokens 透传到 provider 请求。"""
    completions = StubCompletions(
        response=make_response('{"ok": true}')
    )
    client = make_client(completions)

    client.complete_json(
        system_prompt="system",
        user_prompt="user",
        model="deepseek-v4-flash",
        max_tokens=8192,
    )

    assert completions.calls[0]["max_tokens"] == 8192


def test_client_reports_truncation_on_streaming_finish_reason() -> None:
    """C：流式路径 finish_reason=length 同样报 truncated。"""
    completions = StubCompletions(
        response=[
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content='{"step":'),
                        finish_reason=None,
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None),
                        finish_reason="length",
                    )
                ]
            ),
        ]
    )
    client = make_client(completions)

    with pytest.raises(CapabilityError) as captured:
        list(
            client.stream_json_text(
                system_prompt="system",
                user_prompt="user",
                model="deepseek-v4-flash",
            )
        )

    assert captured.value.category == "truncated"


def sdk_error_cases() -> list[
    tuple[Exception, CapabilityErrorCategory, bool]
]:
    request = httpx.Request(
        "POST",
        "https://api.deepseek.com/chat/completions",
    )

    def response(status_code: int) -> httpx.Response:
        return httpx.Response(status_code, request=request)

    return [
        (
            AuthenticationError(
                "sensitive authentication detail",
                response=response(401),
                body=None,
            ),
            "authentication",
            False,
        ),
        (APITimeoutError(request), "timeout", True),
        (
            RateLimitError(
                "sensitive rate-limit detail",
                response=response(429),
                body=None,
            ),
            "rate_limit",
            True,
        ),
        (APIConnectionError(request=request), "service", True),
        (
            APIStatusError(
                "sensitive client-error detail",
                response=response(400),
                body=None,
            ),
            "service",
            False,
        ),
        (
            APIStatusError(
                "sensitive server-error detail",
                response=response(503),
                body=None,
            ),
            "service",
            True,
        ),
    ]


@pytest.mark.parametrize(("sdk_error", "category", "retryable"), sdk_error_cases())
def test_client_normalizes_sdk_errors_without_sensitive_message(
    sdk_error: Exception,
    category: CapabilityErrorCategory,
    retryable: bool,
) -> None:
    client = make_client(StubCompletions(error=sdk_error))

    with pytest.raises(CapabilityError) as captured:
        client.complete_json(
            system_prompt="system",
            user_prompt="user",
            model="deepseek-v4-flash",
        )

    assert captured.value.category == category
    assert captured.value.retryable is retryable
    assert "sensitive" not in captured.value.message
    assert captured.value.__cause__ is sdk_error
