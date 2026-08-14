import pytest
from pydantic import ValidationError

from luxar.domain.repairs import FileReplacement, ProjectFile, RepairPlan


def test_project_file_preserves_valid_nested_relative_path() -> None:
    project_file = ProjectFile(
        path="main/include/blink.h",
        content="#pragma once",
    )

    assert project_file.path == "main/include/blink.h"


def test_replacement_normalizes_windows_separators() -> None:
    replacement = FileReplacement(
        path=r"main\main.c",
        content="void app_main(void) {}",
    )

    assert replacement.path == "main/main.c"


@pytest.mark.parametrize(
    "path",
    [
        "",
        "   ",
        "/etc/passwd",
        r"C:\Users\Gugugu\secret.txt",
        "../outside.c",
        "main/../../outside.c",
        ".",
    ],
)
def test_project_file_rejects_unsafe_path(path: str) -> None:
    with pytest.raises(ValidationError):
        ProjectFile(path=path, content="unsafe")


def test_repair_plan_requires_at_least_one_replacement() -> None:
    with pytest.raises(ValidationError):
        RepairPlan(diagnosis="nothing to apply", replacements=[])


def test_repair_plan_requires_nonempty_diagnosis() -> None:
    with pytest.raises(ValidationError):
        RepairPlan(
            diagnosis="",
            replacements=[FileReplacement(path="main/main.c", content="")],
        )


def test_repair_plan_rejects_duplicate_normalized_targets() -> None:
    with pytest.raises(ValidationError):
        RepairPlan(
            diagnosis="replace the source twice",
            replacements=[
                FileReplacement(path="main/main.c", content="first"),
                FileReplacement(path=r"main\main.c", content="second"),
            ],
        )


def test_repair_plan_preserves_complete_file_content() -> None:
    replacement = FileReplacement(
        path="main/main.c",
        content="#include <stdio.h>\nvoid app_main(void) {}\n",
    )

    repair = RepairPlan(
        diagnosis="add the required declaration",
        replacements=[replacement],
    )

    assert repair.replacements == [replacement]
    assert repair.replacements[0].content.endswith("}\n")
