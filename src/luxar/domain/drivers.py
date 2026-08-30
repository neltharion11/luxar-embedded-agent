"""Public driver-library contracts shared by HTTP, tools, and workflows."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DriverFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str = Field(min_length=1, max_length=500)
    size: int = Field(ge=0, le=1_048_576)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DriverVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    quality: Literal["draft", "build_verified", "hardware_verified"] = "draft"
    build_verified: bool = False
    hardware_verified: bool = False
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class DriverPublishSpec(BaseModel):
    """Metadata plus project-relative files selected for one immutable version."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    driver_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$",
    )
    name: str = Field(min_length=1, max_length=160)
    vendor: str = Field(default="", max_length=120)
    hardware: str = Field(min_length=1, max_length=160)
    protocols: list[str] = Field(min_length=1, max_length=16)
    targets: list[str] = Field(default_factory=list, max_length=32)
    description: str = Field(default="", max_length=2_000)
    file_paths: list[str] = Field(min_length=1, max_length=64)

    @field_validator("protocols", "targets")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().casefold() for value in values if value.strip()]
        if not normalized and values:
            raise ValueError("驱动标签不能为空")
        return list(dict.fromkeys(normalized))

    @field_validator("file_paths")
    @classmethod
    def unique_file_paths(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().replace("\\", "/") for value in values]
        if any(not value for value in normalized):
            raise ValueError("驱动文件路径不能为空")
        return list(dict.fromkeys(normalized))


class DriverManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    driver_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=120)
    version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$",
    )
    name: str = Field(max_length=160)
    vendor: str = Field(default="", max_length=120)
    hardware: str = Field(max_length=160)
    protocols: list[str] = Field(max_length=16)
    targets: list[str] = Field(default_factory=list, max_length=32)
    description: str = Field(default="", max_length=2_000)
    files: list[DriverFile] = Field(min_length=1, max_length=64)
    verification: DriverVerification = Field(default_factory=DriverVerification)
    source_project_key: str = Field(default="", max_length=240)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    published_at: datetime


class DriverCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    driver_id: str
    version: str
    name: str
    vendor: str = ""
    hardware: str
    protocols: list[str]
    targets: list[str]
    description: str = ""
    verification: DriverVerification
    files: list[DriverFile]
    score: float = 0.0
    match_reasons: list[str] = Field(default_factory=list, max_length=20)


class DriverPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    manifest: DriverManifest
    sources: dict[str, str]


def driver_verification_from_result(
    result: Mapping[str, object] | None,
) -> DriverVerification:
    payload = result or {}
    build_verified = payload.get("build_verified") is True
    hardware_verified = payload.get("hardware_function_verified") is True
    raw_evidence = payload.get("evidence_ids", [])
    evidence_ids = (
        [str(item) for item in raw_evidence[:100]]
        if isinstance(raw_evidence, list)
        else []
    )
    quality: Literal["draft", "build_verified", "hardware_verified"] = (
        "hardware_verified"
        if hardware_verified
        else "build_verified"
        if build_verified
        else "draft"
    )
    return DriverVerification(
        quality=quality,
        build_verified=build_verified,
        hardware_verified=hardware_verified,
        evidence_ids=evidence_ids,
    )


__all__ = [
    "DriverCandidate",
    "DriverFile",
    "DriverManifest",
    "DriverPackage",
    "DriverPublishSpec",
    "DriverVerification",
    "driver_verification_from_result",
]
