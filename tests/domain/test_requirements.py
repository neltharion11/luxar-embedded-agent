import pytest
from pydantic import ValidationError

from luxar.domain.requirements import FirmwareRequirement


def test_requirement_without_missing_fields_is_complete() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )

    assert requirement.platform == "espidf"
    assert requirement.is_complete is True


def test_requirement_with_missing_fields_is_incomplete() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        missing_fields=["gpio"],
    )

    assert requirement.is_complete is False
    assert requirement.missing_fields == ["gpio"]


def test_missing_fields_default_is_not_shared_between_requirements() -> None:
    first = FirmwareRequirement(target="esp32", feature="gpio_blink")
    second = FirmwareRequirement(target="esp32", feature="wifi_station")

    first.missing_fields.append("gpio")

    assert second.missing_fields == []


def test_unsupported_platform_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FirmwareRequirement(
            platform="arduino",  # type: ignore[arg-type]
            target="esp32",
            feature="gpio_blink",
        )
