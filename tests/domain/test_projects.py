import pytest
from pydantic import ValidationError

from luxar.domain.projects import ProjectEvidence


def test_project_evidence_accepts_fresh_creation_success() -> None:
    evidence = ProjectEvidence(
        success=True,
        command=["idf.py", "create-project", "blink"],
        return_code=0,
        created_dir="blink",
    )

    assert evidence.success is True
    assert evidence.created_dir == "blink"
    assert evidence.already_existed is False
    assert evidence.error_category is None


def test_project_evidence_accepts_already_existing_project() -> None:
    evidence = ProjectEvidence(
        success=True,
        command=["idf.py", "create-project", "blink"],
        return_code=0,
        created_dir="blink",
        already_existed=True,
    )

    assert evidence.already_existed is True


def test_project_evidence_rejects_success_with_nonzero_return_code() -> None:
    with pytest.raises(ValidationError, match="return_code 0"):
        ProjectEvidence(
            success=True,
            command=["idf.py", "create-project", "blink"],
            return_code=1,
        )


def test_project_evidence_rejects_failure_with_zero_return_code() -> None:
    with pytest.raises(ValidationError, match="return_code 0"):
        ProjectEvidence(
            success=False,
            command=["idf.py", "create-project", "blink"],
            return_code=0,
            error_category="environment",
        )


def test_project_evidence_rejects_success_with_error_category() -> None:
    with pytest.raises(ValidationError, match="error category"):
        ProjectEvidence(
            success=True,
            command=["idf.py", "create-project", "blink"],
            return_code=0,
            error_category="environment",
        )


def test_project_evidence_rejects_already_existed_failure() -> None:
    with pytest.raises(ValidationError, match="must be successful"):
        ProjectEvidence(
            success=False,
            command=["idf.py", "create-project", "blink"],
            return_code=1,
            already_existed=True,
            error_category="environment",
        )


def test_project_evidence_rejects_empty_command() -> None:
    with pytest.raises(ValidationError):
        ProjectEvidence(
            success=True,
            command=[],
            return_code=0,
        )


@pytest.mark.parametrize(
    "created_dir",
    ["../blink", "C:blink", "a/b", ".", "..", "-x"],
)
def test_project_evidence_rejects_unsafe_created_dir(
    created_dir: str,
) -> None:
    with pytest.raises(ValidationError):
        ProjectEvidence(
            success=True,
            command=["idf.py", "create-project", created_dir],
            return_code=0,
            created_dir=created_dir,
        )
