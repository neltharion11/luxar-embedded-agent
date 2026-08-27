"""Compatibility projection for the retired project-scoped Agent rollout."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from luxar.application.continuous_agent_mode import ContinuousAgentSelection


ContinuousAgentProjectMode = Literal["disabled", "shadow", "enabled"]


class ContinuousAgentRolloutPolicy(BaseModel):
    """Expose the old fields while routing solely by the global safety switch."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    global_enabled: bool
    allow_all_enabled: bool
    enabled_projects: list[str] = Field(default_factory=list)
    shadow_projects: list[str] = Field(default_factory=list)
    invalid_tokens: list[str] = Field(default_factory=list)

    def mode_for(self, project_key: str) -> ContinuousAgentProjectMode:
        del project_key
        return "enabled" if self.global_enabled else "disabled"


def select_continuous_agent_rollout(
    selection: ContinuousAgentSelection,
    environ: Mapping[str, str] | None = None,
) -> ContinuousAgentRolloutPolicy:
    # ``environ`` remains in the signature so older embedders do not break, but
    # project allowlists and shadow lists no longer influence routing.
    del environ
    return ContinuousAgentRolloutPolicy(
        global_enabled=selection.enabled,
        allow_all_enabled=selection.enabled,
    )


__all__ = [
    "ContinuousAgentProjectMode",
    "ContinuousAgentRolloutPolicy",
    "select_continuous_agent_rollout",
]
