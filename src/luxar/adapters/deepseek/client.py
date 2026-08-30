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
        repair: bool = False,
        max_tokens: int | None = None,
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


class JsonTextStreamingClient(Protocol):
    """Provider-neutral streaming contract for a JSON-object response body."""

    def stream_json_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int | None = None,
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
        self._thinking_enabled = bool(
            getattr(settings, "thinking_enabled", False)
        )
        self._thinking_effort = str(
            getattr(settings, "thinking_effort", "high")
        )
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
        repair: bool = False,
        max_tokens: int | None = None,
    ) -> dict[str, object]:
        request: dict[str, object] = {
            "model": model,
            "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_prompt,
                    },
                ],
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        self._apply_reasoning_config(request)
        try:
            response = self._client.chat.completions.create(**request)
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

        finish_reason = getattr(response.choices[0], "finish_reason", None)
        content = response.choices[0].message.content

        if content is None or not content.strip():
            raise CapabilityError(
                category="empty_response",
                message=f"{self._provider} response content was empty",
                retryable=True,
            )

        # 输出达到长度上限被截断：与"格式错误"区分开，便于定位根因
        # （OLED 等长任务的内嵌源码/超长 JSON 最容易触发）。
        if finish_reason == "length":
            raise CapabilityError(
                category="truncated",
                message=(
                    f"{self._provider} response reached max output length"
                ),
                retryable=False,
                details={
                    "response_length": len(content),
                    "finish_reason": str(finish_reason),
                },
            )

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            if repair:
                repaired = self._try_repair_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=model,
                    broken_content=content,
                    error=error,
                )
                if repaired is not None:
                    return repaired
            excerpt_start = max(0, error.pos - 160)
            excerpt_end = min(len(content), error.pos + 160)
            raise CapabilityError(
                category="invalid_json",
                message=f"{self._provider} response was not valid JSON",
                retryable=True,
                details={
                    "provider": self._provider,
                    "line": error.lineno,
                    "column": error.colno,
                    "position": error.pos,
                    "response_length": len(content),
                    "response_excerpt": content[excerpt_start:excerpt_end],
                },
            ) from error

        if not isinstance(payload, dict):
            raise CapabilityError(
                category="invalid_json",
                message=f"{self._provider} response must be a JSON object",
                retryable=True,
                details={
                    "provider": self._provider,
                    "response_type": type(payload).__name__,
                },
            )

        return payload

    def _try_repair_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        broken_content: str,
        error: json.JSONDecodeError,
    ) -> dict[str, object] | None:
        """把损坏的 JSON 原文与解析错误位置回给模型修复一次。

        只做一次修复调用（repair=False 避免递归）；修复成功返回 dict，
        失败（语法仍错/非对象/服务异常）返回 None，由调用方继续抛 invalid_json。
        """
        try:
            repaired = self.complete_json(
                system_prompt=(
                    "修复下面 JSON 的语法错误，使其成为合法 JSON object。"
                    "只修复转义、引号、括号和截断/缺失部分，不改变原语义、"
                    "不新增字段、不改变字段结构。只返回修复后的 JSON object，"
                    "不要输出任何其他内容。"
                ),
                user_prompt=json.dumps(
                    {
                        "broken_json": broken_content,
                        "parse_error": {
                            "line": error.lineno,
                            "column": error.colno,
                            "position": error.pos,
                            "message": str(error),
                        },
                    },
                    ensure_ascii=False,
                ),
                model=model,
                repair=False,
            )
        except CapabilityError:
            return None
        if not isinstance(repaired, dict):
            return None
        return repaired

    def stream_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> Iterator[str]:
        """Yield provider deltas instead of slicing a completed response."""

        yield from self._stream_content(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            json_mode=False,
        )

    def stream_json_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Yield the raw JSON-object text as the provider generates it."""

        yield from self._stream_content(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model,
            json_mode=True,
            max_tokens=max_tokens,
        )

    def _stream_content(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        json_mode: bool,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        request: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": True,
        }
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        self._apply_reasoning_config(request)

        try:
            response = self._client.chat.completions.create(**request)
            emitted = False
            finish_reason: str | None = None
            for chunk in response:
                if not chunk.choices:
                    continue
                current_finish = getattr(
                    chunk.choices[0],
                    "finish_reason",
                    None,
                )
                if current_finish:
                    finish_reason = current_finish
                content = chunk.choices[0].delta.content
                if content:
                    emitted = True
                    yield content
            # 输出达到长度上限：与格式错误区分，便于定位长响应截断根因。
            if finish_reason == "length":
                raise CapabilityError(
                    category="truncated",
                    message=(
                        f"{self._provider} streamed response reached "
                        "max output length"
                    ),
                    retryable=False,
                    details={"finish_reason": str(finish_reason)},
                )
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

    def _apply_reasoning_config(self, request: dict[str, object]) -> None:
        if self._provider != "deepseek":
            return
        request["extra_body"] = {
            "thinking": {
                "type": "enabled" if self._thinking_enabled else "disabled"
            }
        }
        if self._thinking_enabled:
            request["reasoning_effort"] = self._thinking_effort


class DeepSeekJsonClient(OpenAICompatibleJsonClient):
    """Backward-compatible name for the original DeepSeek adapter."""
