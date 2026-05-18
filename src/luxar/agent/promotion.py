from __future__ import annotations

from luxar.agent.policy import PolicyDecision, evaluate_promotion


def build_promotion_result(kind: str, current_state: str, evidence_count: int) -> dict[str, object]:
    decision: PolicyDecision = evaluate_promotion(kind=kind, current_state=current_state, evidence_count=evidence_count)
    return {
        "kind": kind,
        "allowed": decision.allowed,
        "reason": decision.reason,
        "target_state": decision.target_state,
        "evidence_count": evidence_count,
    }
