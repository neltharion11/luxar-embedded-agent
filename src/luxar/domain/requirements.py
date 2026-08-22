"""Generic firmware requirements without assuming any peripheral is required."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PeripheralParameter = str | int | float | bool | None


class PeripheralRequirement(BaseModel):
    """One explicitly requested peripheral and only its relevant parameters."""

    model_config = ConfigDict(extra="forbid", strict=True)

    # ESP-IDF supports many peripherals (and custom components), so this must
    # remain extensible rather than becoming another fixed hardware checklist.
    kind: str = Field(min_length=1)
    purpose: str = ""
    instance: str | None = None
    parameters: dict[str, PeripheralParameter] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)


class FirmwareRequirement(BaseModel):
    """A project goal plus zero or more explicitly requested peripherals."""

    model_config = ConfigDict(extra="forbid", strict=True)

    platform: Literal["espidf"] = "espidf"
    target: str
    project_type: Literal["empty", "application"] = "application"
    goal: str
    peripherals: list[PeripheralRequirement] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_gpio_shape(cls, value: object) -> object:
        """Read old checkpoints while always serializing the generic shape."""

        if not isinstance(value, dict):
            return value
        data = dict(value)
        feature = data.pop("feature", None)
        gpio = data.pop("gpio", None)
        if feature is not None and "goal" not in data:
            data["goal"] = str(feature)
        goal = str(data.get("goal", ""))
        if "project_type" not in data:
            data["project_type"] = (
                "empty"
                if goal in {"empty_project", "empty", "minimal_project"}
                else "application"
            )

        root_missing = list(data.get("missing_fields", []))
        legacy_gpio_missing = "gpio" in root_missing
        data["missing_fields"] = [
            field for field in root_missing if field in {"target", "goal"}
        ]
        if "peripherals" not in data:
            needs_gpio = gpio is not None or "gpio" in goal.casefold()
            if needs_gpio or legacy_gpio_missing:
                parameters = {} if gpio is None else {"pin": gpio}
                data["peripherals"] = [
                    {
                        "kind": "gpio",
                        "purpose": goal,
                        "parameters": parameters,
                        "missing_fields": (
                            ["pin"] if legacy_gpio_missing else []
                        ),
                    }
                ]
        return data

    @model_validator(mode="after")
    def validate_project_semantics(self) -> "FirmwareRequirement":
        self.missing_fields = [
            field
            for field in self.missing_fields
            if field in {"target", "goal"}
        ]
        if self.project_type == "empty" and self.peripherals:
            raise ValueError("empty projects cannot require peripherals")
        return self

    @property
    def blocking_missing_fields(self) -> list[str]:
        missing = list(self.missing_fields)
        if not self.target.strip() and "target" not in missing:
            missing.append("target")
        if not self.goal.strip() and "goal" not in missing:
            missing.append("goal")
        for index, peripheral in enumerate(self.peripherals):
            missing.extend(
                f"peripherals[{index}].{field}"
                for field in peripheral.missing_fields
            )
        return missing

    @property
    def is_complete(self) -> bool:
        return not self.blocking_missing_fields
