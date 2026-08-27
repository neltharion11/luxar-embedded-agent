from luxar.application.continuous_agent_mode import select_continuous_agent
from luxar.application.continuous_agent_rollout import (
    select_continuous_agent_rollout,
)


def test_enabled_without_allowlist_routes_every_project_to_v2() -> None:
    policy = select_continuous_agent_rollout(
        select_continuous_agent({}, override=True),
        {},
    )

    assert policy.mode_for("0:test4") == "enabled"
    assert policy.allow_all_enabled is True


def test_retired_allowlist_cannot_disable_other_projects() -> None:
    policy = select_continuous_agent_rollout(
        select_continuous_agent({}, override=True),
        {"LUXAR_CONTINUOUS_AGENT_V2_PROJECTS": "0:test4, blink"},
    )

    assert policy.mode_for("0:test4") == "enabled"
    assert policy.mode_for("2:blink") == "enabled"
    assert policy.mode_for("0:legacy") == "enabled"
    assert policy.enabled_projects == []


def test_retired_shadow_setting_cannot_bypass_global_disable() -> None:
    policy = select_continuous_agent_rollout(
        select_continuous_agent(
            {"LUXAR_CONTINUOUS_AGENT_V2": "0"}
        ),
        {
            "LUXAR_CONTINUOUS_AGENT_V2": "0",
            "LUXAR_CONTINUOUS_AGENT_V2_SHADOW_PROJECTS": "test4",
        },
    )

    assert policy.mode_for("0:test4") == "disabled"
    assert policy.mode_for("0:other") == "disabled"


def test_retired_invalid_allowlist_does_not_change_global_default() -> None:
    policy = select_continuous_agent_rollout(
        select_continuous_agent({}, override=True),
        {"LUXAR_CONTINUOUS_AGENT_V2_PROJECTS": "../all"},
    )

    assert policy.mode_for("0:test4") == "enabled"
    assert policy.allow_all_enabled is True
    assert policy.invalid_tokens == []
