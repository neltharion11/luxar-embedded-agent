import pytest

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient


def test_fake_json_client_returns_responses_in_order_and_records_calls() -> None:
    first = {"target": "esp32"}
    second = {"steps": [{"kind": "build_project"}]}
    client = FakeJsonCompletionClient([first, second])

    first_result = client.complete_json(
        system_prompt="parse requirement",
        user_prompt="blink GPIO 2",
        model="deepseek-v4-flash",
    )
    second_result = client.complete_json(
        system_prompt="create plan",
        user_prompt='{"target": "esp32"}',
        model="deepseek-v4-pro",
    )

    assert first_result == first
    assert second_result == second
    assert client.calls == [
        ("parse requirement", "blink GPIO 2", "deepseek-v4-flash"),
        ("create plan", '{"target": "esp32"}', "deepseek-v4-pro"),
    ]


def test_fake_json_client_returns_a_dictionary_copy() -> None:
    configured = {"target": "esp32"}
    client = FakeJsonCompletionClient([configured])

    result = client.complete_json(
        system_prompt="system",
        user_prompt="user",
        model="model",
    )
    result["target"] = "changed outside the fake"

    assert configured == {"target": "esp32"}


def test_fake_json_client_rejects_unconfigured_extra_call() -> None:
    client = FakeJsonCompletionClient([])

    with pytest.raises(
        RuntimeError,
        match="no configured JSON completion response remaining",
    ):
        client.complete_json(
            system_prompt="system",
            user_prompt="user",
            model="model",
        )

    assert client.calls == [("system", "user", "model")]
