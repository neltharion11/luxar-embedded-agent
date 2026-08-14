import pytest
from pydantic import ValidationError

from luxar.domain.evidence import BuildDiagnostic, BuildEvidence


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


def test_failed_build_evidence_accepts_dependency_failure() -> None:
    evidence = BuildEvidence(
        success=False,
        command=["idf.py", "reconfigure"],
        return_code=1,
        error_category="dependency",
    )

    assert evidence.error_category == "dependency"


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


def test_build_diagnostic_preserves_source_location() -> None:
    diagnostic = BuildDiagnostic(
        file="main/main.c",
        line=42,
        column=5,
        severity="error",
        code="undeclared_identifier",
        message="'gpio_num' undeclared",
    )
    evidence = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=1,
        error_category="source",
        diagnostics=[diagnostic],
    )

    assert evidence.diagnostics == [diagnostic]
    assert evidence.diagnostics[0].file == "main/main.c"
    assert evidence.diagnostics[0].line == 42
    assert evidence.diagnostics[0].column == 5


@pytest.mark.parametrize(("line", "column"), [(0, 1), (1, 0)])
def test_build_diagnostic_rejects_zero_source_positions(
    line: int,
    column: int,
) -> None:
    with pytest.raises(ValidationError):
        BuildDiagnostic(
            line=line,
            column=column,
            severity="error",
            message="compiler error",
        )


def test_build_diagnostic_rejects_empty_message() -> None:
    with pytest.raises(ValidationError):
        BuildDiagnostic(severity="error", message="")


def test_build_evidence_diagnostics_defaults_are_independent() -> None:
    first = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )
    second = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )

    first.diagnostics.append(
        BuildDiagnostic(severity="warning", message="unused variable")
    )

    assert len(first.diagnostics) == 1
    assert second.diagnostics == []
