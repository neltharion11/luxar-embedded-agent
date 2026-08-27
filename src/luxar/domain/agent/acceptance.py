"""验收条件和基于证据 ID 的确定性验收器。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AcceptanceCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    criterion_id: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=2000)
    verification_kind: str = Field(min_length=1, max_length=80)
    status: Literal["pending", "passed", "failed", "blocked"] = "pending"
    required_evidence: list[str] = Field(default_factory=list, max_length=80)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)


class AcceptanceVerification(BaseModel):
    criteria: list[AcceptanceCriterion]
    all_passed: bool
    blocked_criteria: list[str] = Field(default_factory=list)


class AcceptanceVerifier:
    """只依据已采集的证据 ID 更新验收状态，不采信模型自述。"""

    def verify(
        self,
        criteria: Sequence[AcceptanceCriterion],
        evidence_ids: Iterable[str],
    ) -> AcceptanceVerification:
        available = set(evidence_ids)
        updated: list[AcceptanceCriterion] = []
        blocked: list[str] = []
        for criterion in criteria:
            if criterion.status in {"failed", "blocked"}:
                updated.append(criterion)
                blocked.append(criterion.criterion_id)
                continue
            missing = [
                evidence_id
                for evidence_id in criterion.required_evidence
                if evidence_id not in available
            ]
            if missing:
                updated.append(
                    criterion.model_copy(
                        update={"status": "pending", "evidence_ids": []}
                    )
                )
                continue
            matched = [
                evidence_id
                for evidence_id in criterion.required_evidence
                if evidence_id in available
            ]
            updated.append(
                criterion.model_copy(
                    update={"status": "passed", "evidence_ids": matched}
                )
            )

        return AcceptanceVerification(
            criteria=updated,
            all_passed=bool(updated) and all(item.status == "passed" for item in updated),
            blocked_criteria=blocked,
        )

