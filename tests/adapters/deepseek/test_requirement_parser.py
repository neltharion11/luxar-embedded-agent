import json

import pytest
from pydantic import ValidationError

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.requirement_parser import DeepSeekRequirementParser
from luxar.domain.requirements import FirmwareRequirement
from luxar.ports.errors import CapabilityError


def test_parser_converts_complete_json_response_to_requirement() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "platform": "espidf",
                "target": "esp32",
                "feature": "gpio_blink",
                "gpio": 2,
                "missing_fields": [],
            }
        ]
    )
    parser = DeepSeekRequirementParser(
        client=client,
        model="deepseek-v4-flash",
    )

    requirement = parser.parse("让 ESP32 的 GPIO2 闪烁")

    assert requirement == FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )
    assert requirement.is_complete is True


def test_parser_preserves_explicit_missing_fields() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "platform": "espidf",
                "target": "esp32",
                "feature": "gpio_blink",
                "gpio": None,
                "missing_fields": ["gpio"],
            }
        ]
    )
    parser = DeepSeekRequirementParser(client, "deepseek-v4-flash")

    requirement = parser.parse("让 ESP32 的某个 GPIO 闪烁")

    assert requirement.gpio is None
    assert requirement.missing_fields == ["gpio"]
    assert requirement.is_complete is False


def test_parser_sends_schema_task_and_selected_model() -> None:
    task_text = '生成 "ESP32" 程序\n使用 GPIO2'
    client = FakeJsonCompletionClient(
        [
            {
                "platform": "espidf",
                "target": "esp32",
                "feature": "gpio_blink",
                "gpio": 2,
                "missing_fields": [],
            }
        ]
    )
    parser = DeepSeekRequirementParser(client, "deepseek-v4-flash")

    parser.parse(task_text)

    system_prompt, user_prompt, model = client.calls[0]
    assert "JSON Schema" in system_prompt
    assert '"platform"' in system_prompt
    assert '"missing_fields"' in system_prompt
    assert "不要猜测" in system_prompt
    assert json.loads(user_prompt) == {"task_text": task_text}
    assert model == "deepseek-v4-flash"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "platform": "zephyr",
            "target": "esp32",
            "feature": "gpio_blink",
            "missing_fields": [],
        },
        {
            "platform": "espidf",
            "target": "esp32",
            "missing_fields": [],
        },
        {
            "platform": "espidf",
            "target": "esp32",
            "feature": "gpio_blink",
            "gpio": "not-an-integer",
            "missing_fields": [],
        },
    ],
)
def test_parser_normalizes_invalid_domain_schema(
    payload: dict[str, object],
) -> None:
    client = FakeJsonCompletionClient([payload])
    parser = DeepSeekRequirementParser(client, "deepseek-v4-flash")

    with pytest.raises(CapabilityError) as captured:
        parser.parse("invalid model response")

    assert captured.value.category == "invalid_schema"
    assert captured.value.retryable is False
    assert isinstance(captured.value.__cause__, ValidationError)
    assert "zephyr" not in captured.value.message
