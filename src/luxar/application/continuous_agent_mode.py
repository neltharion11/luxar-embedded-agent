"""Feature boundary for the conversation-first continuous Agent runtime.

The continuous runtime is the default Web entry. Keeping its emergency switch
independent from the legacy/supervisor selector makes rollback explicit.
"""

from __future__ import annotations

import os
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict


ContinuousAgentSelectionReason = Literal[
    "default_enabled",
    "explicit_enabled",
    "explicit_disabled",
    "invalid_disabled",
    "injected_override",
]


class ContinuousAgentSelection(BaseModel):
    """Auditable V2 feature selection without exposing environment values."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    enabled: bool
    reason: ContinuousAgentSelectionReason
    explicitly_configured: bool


_ENABLED_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
_DISABLED_VALUES = frozenset({"0", "false", "no", "off", "disabled"})


def select_continuous_agent(
    environ: Mapping[str, str] | None = None,
    *,
    override: bool | None = None,
) -> ContinuousAgentSelection:
    """Resolve the feature once; invalid configuration fails closed."""

    if override is not None:
        return ContinuousAgentSelection(
            enabled=override,
            reason="injected_override",
            explicitly_configured=True,
        )

    values = os.environ if environ is None else environ
    configured = values.get("LUXAR_CONTINUOUS_AGENT_V2")
    if configured is None or not configured.strip():
        return ContinuousAgentSelection(
            enabled=True,
            reason="default_enabled",
            explicitly_configured=False,
        )

    value = configured.strip().lower()
    if value in _ENABLED_VALUES:
        return ContinuousAgentSelection(
            enabled=True,
            reason="explicit_enabled",
            explicitly_configured=True,
        )
    if value in _DISABLED_VALUES:
        return ContinuousAgentSelection(
            enabled=False,
            reason="explicit_disabled",
            explicitly_configured=True,
        )
    return ContinuousAgentSelection(
        enabled=False,
        reason="invalid_disabled",
        explicitly_configured=True,
    )


__all__ = [
    "ContinuousAgentSelection",
    "ContinuousAgentSelectionReason",
    "select_continuous_agent",
]
