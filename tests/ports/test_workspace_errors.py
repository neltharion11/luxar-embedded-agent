import pytest

from luxar.ports.workspace_errors import (
    WorkspaceError,
    WorkspaceErrorCategory,
)


@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        ("invalid_project", False),
        ("unsafe_path", False),
        ("unsupported_file", False),
        ("file_too_large", False),
        ("context_too_large", False),
        ("invalid_encoding", False),
        ("io", True),
        ("rollback_failed", False),
    ],
)
def test_workspace_error_preserves_stable_failure_facts(
    category: WorkspaceErrorCategory,
    retryable: bool,
) -> None:
    error = WorkspaceError(
        category=category,
        message="safe workspace failure",
        retryable=retryable,
    )

    assert error.category == category
    assert error.message == "safe workspace failure"
    assert error.retryable is retryable
    assert str(error) == "safe workspace failure"
