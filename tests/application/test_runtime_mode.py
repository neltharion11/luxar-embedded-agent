from luxar.application.runtime_mode import (
    get_agent_runtime_mode,
    select_firmware_runtime,
)


def test_legacy_is_the_safe_default() -> None:
    assert get_agent_runtime_mode({}) == "legacy"
    assert get_agent_runtime_mode({"LUXAR_AGENT_RUNTIME": "supervisor"}) == "supervisor"
    assert get_agent_runtime_mode({"LUXAR_AGENT_RUNTIME": "unexpected"}) == "legacy"


def test_runtime_selection_explains_explicit_and_invalid_fallbacks() -> None:
    explicit = select_firmware_runtime({"LUXAR_AGENT_RUNTIME": "legacy"})
    invalid = select_firmware_runtime({"LUXAR_AGENT_RUNTIME": "unknown"})

    assert explicit.reason == "explicit_legacy"
    assert explicit.legacy_deprecated is True
    assert explicit.legacy_rollback_support_through == "0.1.x"
    assert invalid.mode == "legacy"
    assert invalid.reason == "invalid_fallback"


def test_injected_runtime_override_is_auditable() -> None:
    selection = select_firmware_runtime({}, override="supervisor")

    assert selection.mode == "supervisor"
    assert selection.reason == "injected_override"
    assert selection.explicitly_configured is True
