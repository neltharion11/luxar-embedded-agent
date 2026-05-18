from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    reason: str
    target_state: str = "draft"


def evaluate_promotion(kind: str, current_state: str, evidence_count: int) -> PolicyDecision:
    if current_state == "validated":
        return PolicyDecision(
            allowed=False,
            reason=f"{kind} is already validated; create a patch candidate instead of force-promoting.",
            target_state=current_state,
        )
    if evidence_count <= 0:
        return PolicyDecision(
            allowed=False,
            reason=f"{kind} promotion requires validation evidence.",
            target_state=current_state or "draft",
        )
    return PolicyDecision(
        allowed=True,
        reason=f"{kind} has evidence and can move to validated.",
        target_state="validated",
    )
