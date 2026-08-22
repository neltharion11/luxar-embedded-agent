import pytest
from pydantic import ValidationError

from luxar.domain.requirements import FirmwareRequirement, PeripheralRequirement


def test_empty_project_is_complete_without_any_peripheral() -> None:
    requirement = FirmwareRequirement(
        target="esp32", project_type="empty", goal="empty_project"
    )

    assert requirement.platform == "espidf"
    assert requirement.peripherals == []
    assert requirement.is_complete is True


def test_explicit_gpio_requirement_can_be_complete() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        project_type="application",
        goal="blink_led",
        peripherals=[
            PeripheralRequirement(
                kind="gpio",
                purpose="drive LED",
                parameters={"pin": 2, "mode": "output"},
            )
        ],
    )

    assert requirement.is_complete is True
    assert requirement.peripherals[0].parameters["pin"] == 2


def test_absent_gpio_does_not_block_an_unrelated_project() -> None:
    requirement = FirmwareRequirement(
        target="esp32s3",
        project_type="application",
        goal="connect_to_wifi",
        peripherals=[PeripheralRequirement(kind="wifi")],
    )

    assert requirement.is_complete is True


def test_only_missing_parameter_required_by_requested_peripheral_blocks() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        project_type="application",
        goal="read_external_sensor_over_i2c",
        peripherals=[
            PeripheralRequirement(
                kind="i2c",
                purpose="read sensor",
                missing_fields=["device_address"],
            )
        ],
    )

    assert requirement.missing_fields == []
    assert requirement.blocking_missing_fields == [
        "peripherals[0].device_address"
    ]
    assert requirement.is_complete is False


def test_blank_goal_is_incomplete() -> None:
    requirement = FirmwareRequirement(
        target="esp32", project_type="application", goal=""
    )

    assert requirement.blocking_missing_fields == ["goal"]
    assert requirement.is_complete is False


def test_mutable_defaults_are_not_shared_between_requirements() -> None:
    first = FirmwareRequirement(target="esp32", goal="first")
    second = FirmwareRequirement(target="esp32", goal="second")

    first.missing_fields.append("goal")
    first.peripherals.append(PeripheralRequirement(kind="uart"))

    assert second.missing_fields == []
    assert second.peripherals == []


def test_empty_project_rejects_invented_peripherals() -> None:
    with pytest.raises(ValidationError):
        FirmwareRequirement(
            target="esp32",
            project_type="empty",
            goal="empty_project",
            peripherals=[PeripheralRequirement(kind="gpio")],
        )


def test_unsupported_platform_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FirmwareRequirement(
            platform="arduino",  # type: ignore[arg-type]
            target="esp32",
            goal="blink_led",
        )


def test_legacy_gpio_checkpoint_migrates_to_generic_shape() -> None:
    requirement = FirmwareRequirement.model_validate(
        {
            "platform": "espidf",
            "target": "esp32",
            "feature": "gpio_blink",
            "gpio": 2,
            "missing_fields": [],
        }
    )

    payload = requirement.model_dump(mode="json")
    assert requirement.goal == "gpio_blink"
    assert requirement.peripherals[0].parameters == {"pin": 2}
    assert "feature" not in payload
    assert "gpio" not in payload
