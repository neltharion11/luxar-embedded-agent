from luxar.application.continuous_agent_mode import select_continuous_agent


def test_continuous_agent_is_enabled_by_default() -> None:
    selection = select_continuous_agent({})

    assert selection.enabled is True
    assert selection.reason == "default_enabled"
    assert selection.explicitly_configured is False


def test_continuous_agent_accepts_explicit_boolean_spellings() -> None:
    for value in ("1", "true", "YES", "on", "enabled"):
        assert select_continuous_agent(
            {"LUXAR_CONTINUOUS_AGENT_V2": value}
        ).enabled is True
    for value in ("0", "false", "NO", "off", "disabled"):
        assert select_continuous_agent(
            {"LUXAR_CONTINUOUS_AGENT_V2": value}
        ).enabled is False


def test_continuous_agent_invalid_configuration_fails_closed() -> None:
    selection = select_continuous_agent(
        {"LUXAR_CONTINUOUS_AGENT_V2": "maybe"}
    )

    assert selection.enabled is False
    assert selection.reason == "invalid_disabled"
    assert selection.explicitly_configured is True


def test_continuous_agent_injected_override_is_auditable() -> None:
    selection = select_continuous_agent({}, override=True)

    assert selection.enabled is True
    assert selection.reason == "injected_override"
    assert selection.explicitly_configured is True
