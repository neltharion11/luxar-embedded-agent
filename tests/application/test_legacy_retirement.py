from __future__ import annotations

import pytest
from pydantic import ValidationError

from luxar.application.legacy_retirement import (
    LEGACY_RETIREMENT_GATES,
    LegacyRetirementObservation,
    current_legacy_retirement,
    evaluate_legacy_retirement,
)


def _passing_observations() -> list[LegacyRetirementObservation]:
    return [
        LegacyRetirementObservation(
            gate_id=gate.gate_id,
            passed=True,
            evidence_ids=[f"test:{gate.gate_id}"],
        )
        for gate in LEGACY_RETIREMENT_GATES
    ]


def test_legacy_removal_requires_every_evidence_backed_gate() -> None:
    report = evaluate_legacy_retirement(_passing_observations()[:-1])

    assert report.ready_for_removal is False
    assert report.blocking_gate_ids == ["supervisor_regression_passed"]


def test_legacy_removal_is_ready_only_after_all_gates_pass() -> None:
    report = evaluate_legacy_retirement(_passing_observations())

    assert report.ready_for_removal is True
    assert report.blocking_gate_ids == []


def test_passed_legacy_removal_gate_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        LegacyRetirementObservation(
            gate_id="rollback_support_window_elapsed",
            passed=True,
        )


def test_current_release_honestly_blocks_legacy_removal() -> None:
    report = current_legacy_retirement()

    assert report.ready_for_removal is False
    assert "rollback_support_window_elapsed" in report.blocking_gate_ids
    assert "specialized_workflows_extracted" not in report.blocking_gate_ids
    specialized = next(
        gate
        for gate in report.gates
        if gate.gate_id == "specialized_workflows_extracted"
    )
    assert specialized.passed is True
    assert specialized.evidence_ids
    regression = next(
        gate
        for gate in report.gates
        if gate.gate_id == "supervisor_regression_passed"
    )
    assert regression.passed is True
    assert regression.evidence_ids
