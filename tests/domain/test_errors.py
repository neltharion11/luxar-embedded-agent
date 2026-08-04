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


@pytest.mark.parametrize(
    ("stage", "category"),
    [
        ("repair", "model_output"),
        ("requirement_analysis", "authentication"),
        ("planning", "rate_limit"),
        ("repair", "service"),
    ],
)
def test_workflow_error_accepts_model_capability_failures(
    stage: str,
    category: str,
) -> None:
    error = WorkflowError(
        stage=stage,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        message="safe application message",
        retryable=False,
    )

    assert error.stage == stage
    assert error.category == category


def test_workflow_error_accepts_workspace_failure() -> None:
    error = WorkflowError(
        stage="repair",
        category="workspace",  # type: ignore[arg-type]
        message="项目工作区操作失败",
        retryable=False,
    )

    assert error.category == "workspace"
