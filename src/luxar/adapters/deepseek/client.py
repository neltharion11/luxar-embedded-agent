"""DeepSeek JSON 客户端：调用兼容 SDK，并把响应和异常转换成稳定 JSON 能力。"""

from __future__ import annotations

import json
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


class DeepSeekJsonClient:
    def __init__(
        self,
        settings: DeepSeekSettings,
        *,
        sdk_client: OpenAI | None = None,
    ) -> None:
        # 测试可以注入假的 SDK Client，避免访问网络。
        if sdk_client is None:
            sdk_client = OpenAI(
                api_key=settings.api_key.get_secret_value(),
                base_url=settings.base_url,
                timeout=settings.timeout_seconds,
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
                message="DeepSeek authentication failed",
                retryable=False,
            ) from error
        except APITimeoutError as error:
            raise CapabilityError(
                category="timeout",
                message="DeepSeek request timed out",
                retryable=True,
            ) from error
        except RateLimitError as error:
            raise CapabilityError(
                category="rate_limit",
                message="DeepSeek rate limit reached",
                retryable=True,
            ) from error
        except APIConnectionError as error:
            raise CapabilityError(
                category="service",
                message="DeepSeek connection failed",
                retryable=True,
            ) from error
        except APIStatusError as error:
            raise CapabilityError(
                category="service",
                message="DeepSeek service rejected the request",
                retryable=error.status_code >= 500,
            ) from error

        if not response.choices:
            raise CapabilityError(
                category="empty_response",
                message="DeepSeek response contained no choices",
                retryable=True,
            )

        content = response.choices[0].message.content

        if content is None or not content.strip():
            raise CapabilityError(
                category="empty_response",
                message="DeepSeek response content was empty",
                retryable=True,
            )

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise CapabilityError(
                category="invalid_json",
                message="DeepSeek response was not valid JSON",
                retryable=True,
            ) from error

        if not isinstance(payload, dict):
            raise CapabilityError(
                category="invalid_json",
                message="DeepSeek response must be a JSON object",
                retryable=True,
            )

        return payload