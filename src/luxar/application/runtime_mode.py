"""legacy/supervisor 固件运行时选择和默认切换边界。

没有通过 Stage 10 资格报告时默认仍是 legacy。显式 supervisor 值用于
旁路/预览验证；只有完整 Definition of Done 报告才能改变无显式配置时的
默认值。
"""

from __future__ import annotations

import os
from typing import Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict


AgentRuntimeMode = Literal["legacy", "supervisor"]
RuntimeSelectionReason = Literal[
    "qualified_default",
    "unqualified_fallback",
    "explicit_legacy",
    "explicit_supervisor",
    "invalid_fallback",
    "injected_override",
]

SUPERVISOR_DEFAULT_SWITCH_VERSION = "0.1.0"
LEGACY_ROLLBACK_SUPPORT_THROUGH = "0.1.x"


class RuntimeQualification(Protocol):
    ready_for_default: bool


class FirmwareRuntimeSelection(BaseModel):
    """可观测、可持久化的固件运行时选择结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mode: AgentRuntimeMode
    reason: RuntimeSelectionReason
    qualification_ready: bool
    explicitly_configured: bool
    legacy_deprecated: bool
    legacy_rollback_available: bool = True
    supervisor_default_switch_version: str = SUPERVISOR_DEFAULT_SWITCH_VERSION
    legacy_rollback_support_through: str = LEGACY_ROLLBACK_SUPPORT_THROUGH


def select_firmware_runtime(
    environ: Mapping[str, str] | None = None,
    *,
    qualification: RuntimeQualification | None = None,
    override: AgentRuntimeMode | None = None,
) -> FirmwareRuntimeSelection:
    """Resolve firmware runtime once and retain the reason for observability."""

    qualification_ready = bool(
        qualification is not None and qualification.ready_for_default
    )
    if override is not None:
        return FirmwareRuntimeSelection(
            mode=override,
            reason="injected_override",
            qualification_ready=qualification_ready,
            explicitly_configured=True,
            legacy_deprecated=override == "legacy",
        )

    values = os.environ if environ is None else environ
    configured = values.get("LUXAR_AGENT_RUNTIME")
    if configured is None or not configured.strip():
        mode: AgentRuntimeMode = (
            "supervisor" if qualification_ready else "legacy"
        )
        return FirmwareRuntimeSelection(
            mode=mode,
            reason=(
                "qualified_default"
                if qualification_ready
                else "unqualified_fallback"
            ),
            qualification_ready=qualification_ready,
            explicitly_configured=False,
            legacy_deprecated=mode == "legacy",
        )

    value = configured.strip().lower()
    if value == "legacy":
        return FirmwareRuntimeSelection(
            mode="legacy",
            reason="explicit_legacy",
            qualification_ready=qualification_ready,
            explicitly_configured=True,
            legacy_deprecated=True,
        )
    if value == "supervisor":
        return FirmwareRuntimeSelection(
            mode="supervisor",
            reason="explicit_supervisor",
            qualification_ready=qualification_ready,
            explicitly_configured=True,
            legacy_deprecated=False,
        )
    return FirmwareRuntimeSelection(
        mode="legacy",
        reason="invalid_fallback",
        qualification_ready=qualification_ready,
        explicitly_configured=True,
        legacy_deprecated=True,
    )


def get_agent_runtime_mode(
    environ: Mapping[str, str] | None = None,
    *,
    qualification: RuntimeQualification | None = None,
) -> AgentRuntimeMode:
    """Compatibility wrapper for callers that only need the selected mode."""

    return select_firmware_runtime(
        environ,
        qualification=qualification,
    ).mode


__all__ = [
    "AgentRuntimeMode",
    "FirmwareRuntimeSelection",
    "LEGACY_ROLLBACK_SUPPORT_THROUGH",
    "RuntimeQualification",
    "RuntimeSelectionReason",
    "SUPERVISOR_DEFAULT_SWITCH_VERSION",
    "get_agent_runtime_mode",
    "select_firmware_runtime",
]
