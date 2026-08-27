"""DeepSeek JSON 客户端：调用兼容 SDK，并把响应和异常转换成稳定 JSON 能力。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from luxar.adapters.deepseek.settings import DeepSeekSettings
from luxar.model_config import ModelEndpoint
from luxar.ports.errors import CapabilityError


class JsonCompletionClient(Protocol):
    """业务 Adapter 依赖的最小 JSON 通信合同。"""

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> dict[str, object]:
        ...


class TextStreamingClient(Protocol):
    """Provider-neutral plain-text streaming contract."""

    def stream_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> Iterator[str]: ...


class OpenAICompatibleJsonClient:
    def __init__(
        self,
        settings: DeepSeekSettings | ModelEndpoint,
        *,
        sdk_client: OpenAI | None = None,
    ) -> None:
        # 测试可以注入假的 SDK Client，避免访问网络。
        self._provider = getattr(settings, "provider", "deepseek")
        if sdk_client is None:
            if isinstance(settings, ModelEndpoint):
                resolved = settings.resolved()
                api_key = resolved.sdk_api_key()
                base_url = resolved.base_url
                timeout = resolved.timeout_seconds
            else:
                api_key = settings.api_key.get_secret_value()
                base_url = settings.base_url
                timeout = settings.timeout_seconds
            sdk_client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=0,
            )

        self._client = sdk_client

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> dict[str, object]:
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
                response_format={
                    "type": "json_object",
                },
            )
        except AuthenticationError as error:
            raise CapabilityError(
                category="authentication",
                message=f"{self._provider} authentication failed",
                retryable=False,
            ) from error
        except APITimeoutError as error:
            raise CapabilityError(
                category="timeout",
                message=f"{self._provider} request timed out",
                retryable=True,
            ) from error
        except RateLimitError as error:
            raise CapabilityError(
                category="rate_limit",
                message=f"{self._provider} rate limit reached",
                retryable=True,
            ) from error
        except APIConnectionError as error:
            raise CapabilityError(
                category="service",
                message=f"{self._provider} connection failed",
                retryable=True,
            ) from error
        except APIStatusError as error:
            raise CapabilityError(
                category="service",
                message=f"{self._provider} service rejected the request",
                retryable=error.status_code >= 500,
            ) from error

        if not response.choices:
            raise CapabilityError(
                category="empty_response",
                message=f"{self._provider} response contained no choices",
                retryable=True,
            )

        content = response.choices[0].message.content

        if content is None or not content.strip():
            raise CapabilityError(
                category="empty_response",
                message=f"{self._provider} response content was empty",
                retryable=True,
            )

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise CapabilityError(
                category="invalid_json",
                message=f"{self._provider} response was not valid JSON",
                retryable=True,
            ) from error

        if not isinstance(payload, dict):
            raise CapabilityError(
                category="invalid_json",
                message=f"{self._provider} response must be a JSON object",
                retryable=True,
            )

        return payload

    def stream_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> Iterator[str]:
        """Yield provider deltas instead of slicing a completed response."""

        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=True,
            )
            emitted = False
            for chunk in response:
                if not chunk.choices:
                    continue
                content = chunk.choices[0].delta.content
                if content:
                    emitted = True
                    yield content
            if not emitted:
                raise CapabilityError(
                    category="empty_response",
                    message=f"{self._provider} streaming response was empty",
                    retryable=True,
                )
        except CapabilityError:
            raise
        except AuthenticationError as error:
            raise CapabilityError(
                category="authentication",
                message=f"{self._provider} authentication failed",
                retryable=False,
            ) from error
        except APITimeoutError as error:
            raise CapabilityError(
                category="timeout",
                message=f"{self._provider} request timed out",
                retryable=True,
            ) from error
        except RateLimitError as error:
            raise CapabilityError(
                category="rate_limit",
                message=f"{self._provider} rate limit reached",
                retryable=True,
            ) from error
        except APIConnectionError as error:
            raise CapabilityError(
                category="service",
                message=f"{self._provider} connection failed",
                retryable=True,
            ) from error
        except APIStatusError as error:
            raise CapabilityError(
                category="service",
                message=f"{self._provider} service rejected the request",
                retryable=error.status_code >= 500,
            ) from error


class DeepSeekJsonClient(OpenAICompatibleJsonClient):
    """Backward-compatible name for the original DeepSeek adapter."""
