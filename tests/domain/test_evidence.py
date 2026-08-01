import pytest
from pydantic import ValidationError

from luxar.domain.evidence import BuildEvidence


def test_successful_build_evidence_preserves_executed_command() -> None:
    evidence = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
        stdout_summary="Project build complete",
    )

    assert evidence.command == ["idf.py", "build"]
    assert evidence.error_category is None


def test_failed_build_evidence_preserves_failure_facts() -> None:
    evidence = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=1,
        stderr_summary="compiler error",
        error_category="source",
    )

    assert evidence.success is False
    assert evidence.error_category == "source"


@pytest.mark.parametrize(
    ("success", "return_code", "error_category"),
    [
        (True, 1, None),
        (False, 0, "unknown"),
        (True, 0, "source"),
    ],
)
def test_build_evidence_rejects_contradictory_facts(
    success: bool,
    return_code: int,
    error_category: str | None,
) -> None:
    with pytest.raises(ValidationError):
        BuildEvidence(
            success=success,
            command=["idf.py", "build"],
            return_code=return_code,
            error_category=error_category,  # type: ignore[arg-type]
        )


def test_build_evidence_requires_an_executed_command() -> None:
    with pytest.raises(ValidationError):
        BuildEvidence(success=False, command=[], return_code=-1)

