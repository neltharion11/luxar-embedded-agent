from __future__ import annotations

import pytest

from luxar.domain.agent.build_recovery import BuildFailureAdvisor
from luxar.domain.evidence import BuildDiagnostic, BuildEvidence


def test_source_failure_targets_compiler_files_and_can_retry_after_repair() -> None:
    decision = BuildFailureAdvisor().analyze(
        BuildEvidence(
            success=False,
            command=["idf.py", "build"],
            return_code=1,
            error_category="source",
            diagnostics=[
                BuildDiagnostic(
                    file="main/main.c",
                    line=17,
                    severity="error",
                    code="E001",
                    message="unknown identifier",
                )
            ],
        )
    )

    assert decision.action == "repair_source"
    assert decision.retryable_after_action is True
    assert decision.requires_approval is False
    assert decision.target_files == ["main/main.c"]
    assert "main/main.c:17" in decision.feedback[1]


def test_dependency_failure_requires_approval_before_resolution() -> None:
    decision = BuildFailureAdvisor().analyze(
        BuildEvidence(
            success=False,
            command=["idf.py", "build"],
            return_code=1,
            error_category="dependency",
        )
    )

    assert decision.action == "resolve_dependency"
    assert decision.requires_approval is True
    assert decision.retryable_after_action is False


def test_successful_build_has_no_recovery_decision() -> None:
    with pytest.raises(ValueError):
        BuildFailureAdvisor().analyze(
            BuildEvidence(
                success=True,
                command=["idf.py", "build"],
                return_code=0,
            )
        )
