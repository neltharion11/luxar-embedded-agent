from __future__ import annotations

import pytest
from pydantic import ValidationError

from luxar.application.runtime_mode import get_agent_runtime_mode
from luxar.application.runtime_qualification import (
    SUPERVISOR_DEFAULT_GATES,
    QualificationObservation,
    current_supervisor_qualification,
    evaluate_supervisor_qualification,
)


def _passing_observations() -> list[QualificationObservation]:
    return [
        QualificationObservation(
            gate_id=gate.gate_id,
            passed=True,
            evidence_ids=[f"test:{gate.gate_id}"],
        )
        for gate in SUPERVISOR_DEFAULT_GATES
    ]


def test_supervisor_default_is_blocked_until_every_definition_of_done_gate() -> None:
    observations = _passing_observations()
    observations = [
        observation
        for observation in observations
        if observation.gate_id != "real_hardware_smoke"
    ]

    report = evaluate_supervisor_qualification(observations)

    assert report.ready_for_default is False
    assert report.missing_gate_ids == ["real_hardware_smoke"]
    assert get_agent_runtime_mode({}, qualification=report) == "legacy"


def test_qualified_supervisor_becomes_default_but_explicit_legacy_still_wins() -> None:
    report = evaluate_supervisor_qualification(_passing_observations())

    assert report.ready_for_default is True
    assert report.missing_gate_ids == []
    assert get_agent_runtime_mode({}, qualification=report) == "supervisor"
    assert (
        get_agent_runtime_mode(
            {"LUXAR_AGENT_RUNTIME": "legacy"},
            qualification=report,
        )
        == "legacy"
    )


def test_explicit_supervisor_preview_does_not_claim_default_qualification() -> None:
    report = evaluate_supervisor_qualification([])

    assert (
        get_agent_runtime_mode(
            {"LUXAR_AGENT_RUNTIME": "supervisor"},
            qualification=report,
        )
        == "supervisor"
    )
    assert report.ready_for_default is False


def test_failed_runtime_comparison_keeps_legacy_as_default() -> None:
    observations = [
        observation
        for observation in _passing_observations()
        if observation.gate_id != "runtime_comparison"
    ]
    observations.append(
        QualificationObservation(
            gate_id="runtime_comparison",
            passed=False,
            note="capability preservation regressed",
        )
    )

    report = evaluate_supervisor_qualification(observations)

    assert report.ready_for_default is False
    assert report.missing_gate_ids == ["runtime_comparison"]
    assert get_agent_runtime_mode({}, qualification=report) == "legacy"


def test_passed_qualification_gate_requires_auditable_evidence() -> None:
    with pytest.raises(ValidationError):
        QualificationObservation(
            gate_id="reference_project",
            passed=True,
            evidence_ids=[],
        )


def test_unknown_qualification_gate_is_rejected() -> None:
    report = evaluate_supervisor_qualification(
        [
            QualificationObservation(
                gate_id="not-in-definition-of-done",
                passed=True,
                evidence_ids=["test:unknown"],
            )
        ]
    )

    assert report.ready_for_default is False
    assert report.unknown_gate_ids == ["not-in-definition-of-done"]


def test_bundled_stage10_release_qualification_enables_default_supervisor() -> None:
    report = current_supervisor_qualification()

    assert report.ready_for_default is True
    assert report.missing_gate_ids == []
    assert report.unknown_gate_ids == []
    assert get_agent_runtime_mode({}, qualification=report) == "supervisor"
    assert all(gate.evidence_ids for gate in report.gates)
