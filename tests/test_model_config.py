from pathlib import Path

from luxar.model_config import (
    ModelConfigStore,
    ModelEndpoint,
    RuntimeModelConfig,
    model_context_window,
)


def test_model_config_store_persists_secret_but_never_exposes_it(tmp_path: Path) -> None:
    path = tmp_path / "model-config.json"
    store = ModelConfigStore(path)
    store.save(RuntimeModelConfig(
        conversation=ModelEndpoint(
            provider="openai",
            api_key="private-openai-key",
            model="gpt-test",
        ),
        vision_mode="python",
    ))

    assert "private-openai-key" in path.read_text(encoding="utf-8")
    loaded = store.load()
    public = loaded.public_dict()
    assert loaded.conversation.api_key is not None
    assert loaded.conversation.api_key.get_secret_value() == "private-openai-key"
    assert "private-openai-key" not in str(public)
    assert public["conversation"]["api_key_configured"] is True  # type: ignore[index]
    assert public["conversation"]["context_window_tokens"] == 128_000  # type: ignore[index]
    assert public["conversation"]["context_compaction_threshold"] == 0.95  # type: ignore[index]
    assert public["conversation"]["thinking_enabled"] is False  # type: ignore[index]
    assert public["embedding"]["mode"] == "local_hash"  # type: ignore[index]
    assert public["embedding"]["configured"] is True  # type: ignore[index]


def test_vision_routing_supports_inherit_separate_and_python() -> None:
    conversation = ModelEndpoint(
        provider="local", base_url="http://127.0.0.1:9000/v1", model="qwen-vl"
    )
    separate = ModelEndpoint(
        provider="local", base_url="http://127.0.0.1:9001/v1", model="ocr-vl"
    )

    inherited = RuntimeModelConfig(
        conversation=conversation, vision_mode="inherit", vision=separate
    )
    independent = inherited.model_copy(update={"vision_mode": "separate"})
    python_only = inherited.model_copy(update={"vision_mode": "python"})

    assert inherited.vision_endpoint().model == "qwen-vl"  # type: ignore[union-attr]
    assert independent.vision_endpoint().model == "ocr-vl"  # type: ignore[union-attr]
    assert python_only.vision_endpoint() is None


def test_local_http_endpoint_does_not_require_api_key() -> None:
    endpoint = ModelEndpoint(
        provider="local",
        base_url="http://127.0.0.1:1234/v1",
        model="qwen2.5-coder",
    )

    assert endpoint.configured is True
    assert endpoint.sdk_api_key() == "local"


def test_loopback_http_endpoint_can_be_detected_without_local_provider() -> None:
    endpoint = ModelEndpoint(
        provider="openai",
        base_url="http://localhost:11434/v1",
        model="llama3.1:8b",
    )

    assert endpoint.configured is True
    assert endpoint.sdk_api_key() == "local"
    assert endpoint.uses_local_execution is True


def test_remote_endpoint_uses_online_execution_policy() -> None:
    endpoint = ModelEndpoint(
        provider="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )

    assert endpoint.uses_local_execution is False


def test_environment_only_local_http_configuration_is_detected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("LUXAR_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LUXAR_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("LUXAR_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("LUXAR_LLM_MODEL", "local-chat")

    config = ModelConfigStore(tmp_path / "missing.json").load()

    assert config.conversation.provider == "local"
    assert config.conversation.configured is True


def test_model_context_window_uses_catalog_and_explicit_override() -> None:
    assert model_context_window("deepseek", "deepseek-v4-pro") == 1_048_576
    assert model_context_window("deepseek", "deepseek-v4-flash") == 1_048_576
    assert model_context_window("local", "custom") == 32_768
    assert model_context_window("local", "custom", 262_144) == 262_144
