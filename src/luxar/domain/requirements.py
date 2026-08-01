from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FirmwareRequirement(BaseModel):
    platform: Literal["espidf"] = "espidf"
    target: str
    feature: str
    gpio: int | None = None
    missing_fields: list[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields