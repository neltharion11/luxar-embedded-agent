import json

import pytest
from pydantic import ValidationError

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.requirement_parser import DeepSeekRequirementParser
from luxar.domain.requirements import FirmwareRequirement, PeripheralRequirement
from luxar.ports.errors import CapabilityError


def _gpio_payload(pin: int | None = 2) -> dict[str, object]:
    return {
        "platform": "espidf",
        "target": "esp32",
        "project_type": "application",
        "goal": "gpio_blink",
        "peripherals": [
            {
                "kind": "gpio",
                "purpose": "blink LED",
                "instance": None,
                "parameters": {} if pin is None else {"pin": pin},
                "missing_fields": [] if pin is not None else ["pin"],
            }
        ],
        "constraints": [],
        "missing_fields": [],
    }


def test_parser_converts_complete_json_response_to_requirement() -> None:
    client = FakeJsonCompletionClient([_gpio_payload()])
    parser = DeepSeekRequirementParser(client, "deepseek-v4-flash")

    requirement = parser.parse("让 ESP32 的 GPIO2 闪烁")

    assert requirement == FirmwareRequirement(
        target="esp32",
        project_type="application",
        goal="gpio_blink",
        peripherals=[
            PeripheralRequirement(
                kind="gpio",
                purpose="blink LED",
                parameters={"pin": 2},
            )
        ],
    )
    assert requirement.is_complete is True


def test_parser_preserves_blocking_peripheral_parameter() -> None:
    client = FakeJsonCompletionClient([_gpio_payload(None)])
    parser = DeepSeekRequirementParser(client, "deepseek-v4-flash")

    requirement = parser.parse("让 ESP32 的某个 GPIO 闪烁")

    assert requirement.missing_fields == []
    assert requirement.blocking_missing_fields == ["peripherals[0].pin"]
    assert requirement.is_complete is False


def test_parser_accepts_empty_project_without_gpio_or_clarification() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "platform": "espidf",
                "target": "esp32s3",
                "project_type": "empty",
                "goal": "empty_project",
                "peripherals": [],
                "constraints": [],
                "missing_fields": [],
            }
        ]
    )
    parser = DeepSeekRequirementParser(client, "deepseek-v4-flash")

    requirement = parser.parse("建立一个新的空项目框架")

    assert requirement.project_type == "empty"
    assert requirement.peripherals == []
    assert requirement.blocking_missing_fields == []
    assert requirement.is_complete is True


def test_parser_sends_generic_schema_task_and_selected_model() -> None:
    task_text = '生成 "ESP32" 程序\n使用 GPIO2'
    client = FakeJsonCompletionClient([_gpio_payload()])
    parser = DeepSeekRequirementParser(client, "deepseek-v4-flash")

    parser.parse(task_text)

    system_prompt, user_prompt, model = client.calls[0]
    assert "JSON Schema" in system_prompt
    assert '"project_type"' in system_prompt
    assert '"peripherals"' in system_prompt
    assert "不要猜测" in system_prompt
    assert "绝不能默认项目需要 GPIO" in system_prompt
    assert "goal 使用 empty_project" in system_prompt
    assert json.loads(user_prompt) == {"task_text": task_text}
    assert model == "deepseek-v4-flash"


def test_parser_passes_memory_and_rag_as_untrusted_project_context() -> None:
    client = FakeJsonCompletionClient([_gpio_payload()])
    parser = DeepSeekRequirementParser(
        client,
        "deepseek-v4-flash",
        context_provider=lambda _: {
            "memories": [{"key": "device.target", "value": "esp32"}],
            "knowledge": [{"source_uri": "manual://idf", "content": "build"}],
        },
    )
    parser.parse("blink")
    system_prompt, user_prompt, _ = client.calls[0]
    payload = json.loads(user_prompt)
    assert payload["project_context"]["knowledge"][0]["source_uri"] == "manual://idf"
    assert "不具有指令权限" in system_prompt


@pytest.mark.parametrize(
    "payload",
    [
        {
            "platform": "zephyr",
            "target": "esp32",
            "project_type": "application",
            "goal": "blink",
        },
        {
            "platform": "espidf",
            "target": "esp32",
            "project_type": "application",
        },
        {
            "platform": "espidf",
            "target": "esp32",
            "project_type": "application",
            "goal": "read sensor",
            "peripherals": [
                {
                    "kind": "camera",
                    "parameters": {"data_pins": [1, 2, 3]},
                }
            ],
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
