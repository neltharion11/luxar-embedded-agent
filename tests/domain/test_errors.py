import pytest
from pydantic import ValidationError

from luxar.domain.errors import WorkflowError


def test_workflow_error_preserves_normalized_recovery_information() -> None:
    error = WorkflowError(
        stage="build",
        category="environment",
        message="ESP-IDF executable was not found",
        retryable=False,
        user_suggestion="Install and configure ESP-IDF",
        evidence_reference="logs/build-001.txt",
    )

    assert error.retryable is False
    assert error.evidence_reference == "logs/build-001.txt"


def test_workflow_error_rejects_unknown_stage_names() -> None:
    with pytest.raises(ValidationError):
        WorkflowError(
            stage="random_sdk_stage",  # type: ignore[arg-type]
            category="unknown",
            message="provider-specific exception",
            retryable=False,
        )
