from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    reason: str
    target_state: str = "draft"


def evaluate_promotion(kind: str, current_state: str, evidence_count: int, attributes: dict[str, Any] | None = None) -> PolicyDecision:
    attrs = attributes or {}
    
    if current_state == "validated":
        return PolicyDecision(
            allowed=False,
            reason=f"{kind} is already validated; create a patch candidate instead of force-promoting.",
            target_state=current_state,
        )

    if kind == "executable" and not attrs.get("sandboxed_dry_run_passed"):
        return PolicyDecision(
            allowed=False,
            reason="Executable skills must pass a sandboxed dry-run before promotion.",
            target_state=current_state or "draft",
        )

    if kind == "lesson" and not attrs.get("promotable", True):
        return PolicyDecision(
            allowed=False,
            reason="This lesson is explicitly marked as not promotable.",
            target_state=current_state or "draft",
        )

    if evidence_count <= 0:
        return PolicyDecision(
            allowed=False,
            reason=f"{kind} promotion requires validation evidence.",
            target_state=current_state or "draft",
        )

    return PolicyDecision(
        allowed=True,
        reason=f"{kind} has evidence and passed mechanical guardrails, can move to validated.",
        target_state="validated",
    )
