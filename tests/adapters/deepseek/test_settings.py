import pytest
from pydantic import ValidationError

from luxar.adapters.deepseek.settings import DeepSeekSettings


def test_settings_use_current_deepseek_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-key")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_FAST_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_REPAIR_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_TIMEOUT_SECONDS", raising=False)

    settings = DeepSeekSettings()

    assert settings.base_url == "https://api.deepseek.com"
    assert settings.fast_model == "deepseek-v4-flash"
    assert settings.repair_model == "deepseek-v4-pro"
    assert settings.timeout_seconds == 60.0
    assert settings.api_key.get_secret_value() == "test-secret-key"


def test_settings_read_environment_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "overridden-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://deepseek.example.test")
    monkeypatch.setenv("DEEPSEEK_FAST_MODEL", "fast-test-model")
    monkeypatch.setenv("DEEPSEEK_REPAIR_MODEL", "repair-test-model")
    monkeypatch.setenv("DEEPSEEK_TIMEOUT_SECONDS", "12.5")

    settings = DeepSeekSettings()

    assert settings.base_url == "https://deepseek.example.test"
    assert settings.fast_model == "fast-test-model"
    assert settings.repair_model == "repair-test-model"
    assert settings.timeout_seconds == 12.5


def test_settings_repr_does_not_expose_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "must-never-appear-in-repr"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)

    settings = DeepSeekSettings()

    assert secret not in repr(settings)
    assert "**********" in repr(settings)


def test_settings_require_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ValidationError):
        DeepSeekSettings()


def test_settings_reject_nonpositive_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret-key")
    monkeypatch.setenv("DEEPSEEK_TIMEOUT_SECONDS", "0")

    with pytest.raises(ValidationError):
        DeepSeekSettings()
