from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from luxar.core.config_manager import AgentConfig


class LLMClientError(RuntimeError):
    """Raised when the configured LLM provider cannot complete a request."""


@dataclass(slots=True)
class LLMResponse:
    provider: str
    model: str
    content: str
    raw: dict
    tool_calls: list | None = None
    reasoning_content: str = ""


@dataclass
class ToolCall:
    id: str
    function_name: str
    arguments: dict


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, LLMClientError):
        msg = str(exc)
        if msg.startswith("LLM request failed with HTTP 429"):
            return True
        if msg.startswith("LLM request failed with HTTP 5"):
            return True
        if msg.startswith("LLM request failed: "):
            return True
    return False


# OpenAI-compatible provider registry: endpoint, api_key_env, and model presets (id, name, context window)
_OPENAI_PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "endpoint": "https://api.deepseek.com/chat/completions",
        "key_env": "DEEPSEEK_API_KEY",
        "models": [
            {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash", "context": 393216},
            {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "context": 393216},
        ],
    },
    "openai": {
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "key_env": "OPENAI_API_KEY",
        "models": [
            {"id": "gpt-5.5", "name": "GPT-5.5", "context": 131072},
            {"id": "gpt-5.4", "name": "GPT-5.4", "context": 131072},
            {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini", "context": 131072},
            {"id": "gpt-5.4-nano", "name": "GPT-5.4 Nano", "context": 131072},
        ],
    },
    "groq": {
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "key_env": "GROQ_API_KEY",
        "models": [
            {"id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B", "context": 131072},
            {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B", "context": 32768},
            {"id": "openai/gpt-oss-120b", "name": "GPT-OSS 120B", "context": 65536},
            {"id": "openai/gpt-oss-20b", "name": "GPT-OSS 20B", "context": 65536},
        ],
    },
    "xai": {
        "endpoint": "https://api.xai.com/v1/chat/completions",
        "key_env": "XAI_API_KEY",
        "models": [
            {"id": "grok-4.20", "name": "Grok 4.20", "context": 131072},
            {"id": "grok-3", "name": "Grok 3", "context": 131072},
        ],
    },
    "together": {
        "endpoint": "https://api.together.xyz/v1/chat/completions",
        "key_env": "TOGETHER_API_KEY",
        "models": [
            {"id": "deepseek-ai/DeepSeek-V3", "name": "DeepSeek V3", "context": 131072},
            {"id": "Qwen/Qwen3-Coder-480B-A35B-Instruct", "name": "Qwen3 Coder 480B", "context": 131072},
            {"id": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", "name": "Llama 4 Maverick 17B", "context": 131072},
        ],
    },
    "fireworks": {
        "endpoint": "https://api.fireworks.ai/inference/v1/chat/completions",
        "key_env": "FIREWORKS_API_KEY",
        "models": [
            {"id": "accounts/fireworks/models/qwen2p5-coder-32b-instruct", "name": "Qwen 2.5 Coder 32B", "context": 131072},
            {"id": "accounts/fireworks/models/deepseek-v3", "name": "DeepSeek V3", "context": 131072},
        ],
    },
    "cerebras": {
        "endpoint": "https://api.cerebras.ai/v1/chat/completions",
        "key_env": "CEREBRAS_API_KEY",
        "models": [
            {"id": "llama3.3-70b", "name": "Llama 3.3 70B", "context": 128000},
            {"id": "llama3.1-8b", "name": "Llama 3.1 8B", "context": 128000},
        ],
    },
    "moonshot": {
        "endpoint": "https://api.moonshot.cn/v1/chat/completions",
        "key_env": "MOONSHOT_API_KEY",
        "models": [
            {"id": "kimi-k2.6", "name": "Kimi K2.6", "context": 262144},
            {"id": "kimi-k2.5", "name": "Kimi K2.5", "context": 262144},
        ],
    },
    "zhipu": {
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "key_env": "ZHIPU_API_KEY",
        "models": [
            {"id": "glm-5.1", "name": "GLM-5.1 (Flagship)", "context": 131072},
            {"id": "glm-5", "name": "GLM-5", "context": 131072},
            {"id": "glm-4.7", "name": "GLM-4.7", "context": 131072},
            {"id": "glm-4.7-flash", "name": "GLM-4.7 Flash (Free)", "context": 131072},
        ],
    },
    "openrouter": {
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        "models": [
            {"id": "deepseek/deepseek-chat", "name": "DeepSeek V3 Chat", "context": 65536},
            {"id": "openai/gpt-4o", "name": "GPT-4o", "context": 128000},
            {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4", "context": 200000},
            {"id": "qwen/qwen3-coder", "name": "Qwen3 Coder", "context": 131072},
        ],
    },
    "deepinfra": {
        "endpoint": "https://api.deepinfra.com/v1/openai/chat/completions",
        "key_env": "DEEPINFRA_API_KEY",
        "models": [
            {"id": "deepseek-ai/DeepSeek-V3", "name": "DeepSeek V3", "context": 131072},
            {"id": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", "name": "Llama 4 Maverick 17B", "context": 131072},
        ],
    },
    "minimax": {
        "endpoint": "https://api.minimax.io/v1/chat/completions",
        "key_env": "MINIMAX_API_KEY",
        "models": [
            {"id": "MiniMax-M2.1", "name": "MiniMax M2.1", "context": 262144},
            {"id": "MiniMax-M2.7", "name": "MiniMax M2.7", "context": 262144},
        ],
    },
    "ollama": {
        "endpoint": "http://localhost:11434/v1/chat/completions",
        "key_env": "",
        "models": [
            {"id": "qwen2.5-coder:7b", "name": "Qwen 2.5 Coder 7B", "context": 32768},
            {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "context": 128000},
            {"id": "deepseek-r1:8b", "name": "DeepSeek R1 8B", "context": 131072},
            {"id": "codellama:7b", "name": "CodeLlama 7B", "context": 16384},
        ],
    },
    "custom": {
        "endpoint": "",
        "key_env": "",
        "models": [],
    },
}


class LLMClient:
    _soul_cache: str | None = None
    _manual_cache: str | None = None

    def __init__(self, config: AgentConfig):
        self.config = config
        self.provider = config.llm.provider.strip().lower()
        self.model = config.llm.model
        self.temperature = config.llm.temperature
        self.max_tokens = config.llm.max_tokens
        self.timeout_sec = config.llm.timeout_sec
        self.base_url = config.llm.base_url.strip()
        self.retry_attempts = config.llm.retry_attempts
        self.retry_min_delay = config.llm.retry_min_delay
        self.retry_max_delay = config.llm.retry_max_delay

    def _resolve_provider(self) -> tuple[str, str, str]:
        """Returns (provider_type, endpoint, api_key).
        provider_type is 'openai_compatible' or 'claude'; raises LLMClientError if unknown."""
        if self.provider == "claude":
            default = "https://api.anthropic.com/v1/messages"
            if self.base_url:
                base = self.base_url.rstrip("/")
                endpoint = base if base.endswith("/v1/messages") else base + "/v1/messages"
            else:
                endpoint = default
            return ("claude", endpoint, self._read_api_key("ANTHROPIC_API_KEY"))
        info = _OPENAI_PROVIDERS.get(self.provider)
        if info is not None:
            default = info["endpoint"]
            if self.base_url:
                path = urllib.parse.urlparse(default).path
                base = self.base_url.rstrip("/")
                endpoint = base if base.endswith(path) else base + path
            else:
                endpoint = default
            key_env = info["key_env"]
            api_key = self._read_api_key(key_env) if key_env else ""
            return ("openai_compatible", endpoint, api_key)
        raise LLMClientError(f"Unsupported LLM provider: {self.provider}")

    @classmethod
    def _read_text_file(cls, path: Path) -> str | None:
        if cls._soul_cache is not None and "soul" in str(path):
            return cls._soul_cache
        if cls._manual_cache is not None and "manual" in str(path):
            return cls._manual_cache
        if not path.is_file():
            return None
        try:
            content = path.read_text(encoding="utf-8").strip()
        except Exception:
            return None
        if "soul" in str(path):
            cls._soul_cache = content
        if "manual" in str(path):
            cls._manual_cache = content
        return content

    @classmethod
    def _find_project_root(cls) -> Path:
        return Path(__file__).resolve().parent.parent.parent.parent

    @classmethod
    def load_soul(cls) -> str | None:
        return cls._read_text_file(cls._find_project_root() / "soul.md")

    @classmethod
    def load_agent_manual(cls) -> str | None:
        return cls._read_text_file(cls._find_project_root() / "agent.md")

    @classmethod
    def build_system_prompt(cls, task_prompt: str = "") -> str:
        parts: list[str] = []
        soul = cls.load_soul()
        manual = cls.load_agent_manual()
        if soul:
            parts.append(soul)
        if manual:
            parts.append(manual)
        if task_prompt:
            parts.append(task_prompt)
        return "\n\n---\n\n".join(parts)

    def complete(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        enriched = self.build_system_prompt(task_prompt=system_prompt)
        return self._complete(messages=None, prompt=prompt, system_prompt=enriched)

    def complete_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ):
        enriched = self.build_system_prompt()
        ptype, endpoint, api_key = self._resolve_provider()
        if ptype == "openai_compatible":
            yield from self._stream_chat_completions(
                provider=self.provider,
                endpoint=endpoint,
                api_key=api_key,
                messages=messages,
                system_prompt=enriched,
                tools=tools,
            )
        else:
            resp = self.complete_with_tools(messages=messages, tools=tools)
            yield {"type": "token", "content": resp.content}

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        enriched = self.build_system_prompt()
        ptype, endpoint, api_key = self._resolve_provider()
        if ptype == "openai_compatible":
            return self._complete_chat_completions(
                provider=self.provider,
                endpoint=endpoint,
                api_key=api_key,
                messages=messages,
                system_prompt=enriched,
                tools=tools,
            )
        if ptype == "claude":
            return self._complete_anthropic(
                endpoint=endpoint,
                api_key=api_key,
                prompt=messages[-1]["content"] if messages else "",
                system_prompt=enriched,
            )
        raise LLMClientError(f"Unsupported LLM provider: {self.provider}")

    def _complete(
        self,
        messages: list[dict] | None,
        prompt: str,
        system_prompt: str,
    ) -> LLMResponse:
        ptype, endpoint, api_key = self._resolve_provider()
        if ptype == "openai_compatible":
            return self._complete_chat_completions(
                provider=self.provider,
                endpoint=endpoint,
                api_key=api_key,
                messages=messages,
                prompt=prompt,
                system_prompt=system_prompt,
                tools=None,
            )
        if ptype == "claude":
            return self._complete_anthropic(
                endpoint=endpoint,
                api_key=api_key,
                prompt=prompt,
                system_prompt=system_prompt,
            )
        raise LLMClientError(f"Unsupported LLM provider: {self.provider}")

    def _complete_chat_completions(
        self,
        provider: str,
        endpoint: str,
        api_key: str,
        messages: list[dict] | None = None,
        prompt: str = "",
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        msgs: list[dict[str, str]] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        if messages:
            msgs.extend(messages)
        elif prompt:
            msgs.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        raw = self._post_json(
            endpoint=endpoint,
            payload=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        choice = raw.get("choices", [{}])[0].get("message", {})
        content = choice.get("content") or ""
        raw_tool_calls = choice.get("tool_calls")

        tool_calls: list[ToolCall] | None = None
        if raw_tool_calls:
            tool_calls = []
            for tc in raw_tool_calls:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    function_name=tc["function"]["name"],
                    arguments=args,
                ))

        return LLMResponse(provider=provider, model=self.model, content=content, raw=raw, tool_calls=tool_calls,
                           reasoning_content=choice.get("reasoning_content", ""))

    def _stream_chat_completions(
        self,
        provider: str,
        endpoint: str,
        api_key: str,
        messages: list[dict],
        system_prompt: str = "",
        tools: list[dict] | None = None,
    ):
        msgs: list[dict[str, str]] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        if messages:
            msgs.extend(messages)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout_sec)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMClientError(f"LLM stream request failed with HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise LLMClientError(f"LLM stream request failed: {exc.reason}") from exc

        buffer = b""
        for chunk_bytes in iter(lambda: response.read(4096), b""):
            buffer += chunk_bytes
            while b"\n" in buffer:
                line_bytes, buffer = buffer.split(b"\n", 1)
                line = line_bytes.decode("utf-8").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    reasoning = delta.get("reasoning_content", "")
                    if content:
                        yield {"type": "token", "content": content}
                    if reasoning:
                        yield {"type": "token", "content": "", "reasoning_content": reasoning}
                    tc_delta = delta.get("tool_calls")
                    if tc_delta:
                        for tc in tc_delta:
                            fn = tc.get("function", {})
                            yield {"type": "tool_call", "id": tc.get("id", ""), "name": fn.get("name", ""), "arguments": fn.get("arguments", "")}
        # Process remaining data after stream ends
        if buffer.strip():
            try:
                line = buffer.decode("utf-8", errors="replace").strip()
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str != "[DONE]":
                        chunk = json.loads(data_str)
                        content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content:
                            yield {"type": "token", "content": content}
            except Exception:
                pass

    def _complete_anthropic(
        self,
        endpoint: str,
        api_key: str,
        prompt: str,
        system_prompt: str,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt
        raw = self._post_json(
            endpoint=endpoint,
            payload=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        try:
            blocks = raw["content"]
            content = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
        except (KeyError, TypeError) as exc:
            raise LLMClientError("claude response did not contain message content") from exc
        if not content:
            raise LLMClientError("claude response returned no text content")
        return LLMResponse(provider="claude", model=self.model, content=content, raw=raw)

    def _read_api_key(self, default_env_name: str) -> str:
        env_name = self.config.llm.api_key_env.strip() or default_env_name
        if env_name:
            value = os.getenv(env_name, "").strip()
            if value:
                return value
        stored = getattr(self.config, "api_keys", {}) or {}
        value = stored.get(self.provider, "").strip()
        if value:
            return value
        raise LLMClientError(
            f"Missing API key. Set the `{env_name or default_env_name}` environment variable or configure an API key for provider '{self.provider}' in Model Config."
        )

    def _make_retry_decorator(self) -> Callable:
        def _before_sleep(retry_state):
            attempt = retry_state.attempt_number
            exc = retry_state.outcome.exception()
            import logging
            logging.getLogger(__name__).warning(
                "LLM request attempt %d failed: %s. Retrying...", attempt, exc
            )

        return retry(
            stop=stop_after_attempt(self.retry_attempts),
            wait=wait_exponential(
                multiplier=1,
                min=self.retry_min_delay,
                max=self.retry_max_delay,
            ),
            retry=retry_if_exception(_is_retryable),
            before_sleep=_before_sleep,
            reraise=True,
        )

    def _post_json(self, endpoint: str, payload: dict, headers: dict[str, str]) -> dict:
        decorator = self._make_retry_decorator()

        @decorator
        def _do_post() -> dict:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                    body = response.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                raise LLMClientError(f"LLM request failed with HTTP {exc.code}: {body}") from exc
            except urllib.error.URLError as exc:
                raise LLMClientError(f"LLM request failed: {exc.reason}") from exc

            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                raise LLMClientError("LLM response was not valid JSON") from exc

        return _do_post()

