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
from luxar.ports.errors import CapabilityError, CapabilityErrorCategory


class StubCompletions:
    def __init__(
        self,
        *,
        response: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        return self.response


class StubSdkClient:
    def __init__(self, completions: StubCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def make_response(content: str | None) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]
    )


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
        }
    ]


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
