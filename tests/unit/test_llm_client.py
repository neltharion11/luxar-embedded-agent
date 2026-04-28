from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient, LLMClientError


class _FakeHttpResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _error_response(status: int, body: str = ""):
    return urllib.error.HTTPError(
        url="http://example.com",
        code=status,
        msg="error",
        hdrs={},
        fp=io.BytesIO(body.encode("utf-8")),
    )


class LLMClientTests(unittest.TestCase):
    def test_missing_api_key_raises_clear_error(self) -> None:
        config = AgentConfig()
        client = LLMClient(config)
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(LLMClientError) as ctx:
                client.complete("hello")
        self.assertIn("DEEPSEEK_API_KEY", str(ctx.exception))

    def test_openai_style_provider_parses_chat_completion(self) -> None:
        config = AgentConfig()
        config.llm.provider = "openai"
        config.llm.model = "gpt-test"
        client = LLMClient(config)

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            with mock.patch(
                "urllib.request.urlopen",
                return_value=_FakeHttpResponse(
                    {"choices": [{"message": {"content": "generated text"}}]}
                ),
            ) as urlopen_mock:
                response = client.complete("hello", system_prompt="system")

        self.assertEqual("generated text", response.content)
        request = urlopen_mock.call_args.args[0]
        self.assertEqual("https://api.openai.com/v1/chat/completions", request.full_url)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual("gpt-test", body["model"])
        system_content = body["messages"][0]["content"]
        self.assertIn("Luxar Soul", system_content)
        self.assertIn("Inviolable Rules", system_content)
        self.assertIn("Luxar Agent Manual", system_content)
        self.assertIn("system", system_content)

    def test_claude_provider_parses_message_blocks(self) -> None:
        config = AgentConfig()
        config.llm.provider = "claude"
        config.llm.model = "claude-test"
        client = LLMClient(config)

        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            with mock.patch(
                "urllib.request.urlopen",
                return_value=_FakeHttpResponse(
                    {"content": [{"type": "text", "text": "semantic review output"}]}
                ),
            ):
                response = client.complete("hello")

        self.assertEqual("semantic review output", response.content)

    def test_transient_429_retries_and_succeeds(self) -> None:
        config = AgentConfig()
        config.llm.provider = "openai"
        config.llm.model = "gpt-test"
        config.llm.retry_attempts = 3
        config.llm.retry_min_delay = 0
        config.llm.retry_max_delay = 1
        client = LLMClient(config)

        call_count = 0

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise _error_response(429, "rate limited")
            return _FakeHttpResponse({"choices": [{"message": {"content": "ok"}}]})

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            with mock.patch("urllib.request.urlopen", side_effect=_side_effect):
                response = client.complete("hello")
        self.assertEqual("ok", response.content)
        self.assertEqual(3, call_count)

    def test_retry_exhaustion_raises_last_error(self) -> None:
        config = AgentConfig()
        config.llm.provider = "openai"
        config.llm.model = "gpt-test"
        config.llm.retry_attempts = 2
        config.llm.retry_min_delay = 0
        config.llm.retry_max_delay = 1
        client = LLMClient(config)

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            with mock.patch(
                "urllib.request.urlopen",
                side_effect=_error_response(503, "service unavailable"),
            ):
                with self.assertRaises(LLMClientError) as ctx:
                    client.complete("hello")
        self.assertIn("503", str(ctx.exception))

    def test_non_retryable_4xx_raises_immediately(self) -> None:
        config = AgentConfig()
        config.llm.provider = "openai"
        config.llm.model = "gpt-test"
        config.llm.retry_attempts = 3
        config.llm.retry_min_delay = 0
        config.llm.retry_max_delay = 1
        client = LLMClient(config)

        call_count = 0

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise _error_response(400, "bad request")

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            with mock.patch("urllib.request.urlopen", side_effect=_side_effect):
                with self.assertRaises(LLMClientError) as ctx:
                    client.complete("hello")
        self.assertIn("400", str(ctx.exception))
        self.assertEqual(1, call_count)

    def test_retry_config_defaults_are_reasonable(self) -> None:
        config = AgentConfig()
        self.assertEqual(config.llm.retry_attempts, 3)
        self.assertEqual(config.llm.retry_min_delay, 2)
        self.assertEqual(config.llm.retry_max_delay, 30)


if __name__ == "__main__":
    unittest.main()

