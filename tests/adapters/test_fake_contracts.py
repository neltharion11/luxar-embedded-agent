from pathlib import Path

import pytest

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_repair_planner import FakeRepairPlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.repairs import FileReplacement, ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


def test_fake_requirement_parser_returns_configured_requirement_and_records_call() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )
    parser = FakeRequirementParser(requirement)

    result = parser.parse("create an ESP32 GPIO blink project")

    assert result is requirement
    assert parser.calls == ["create an ESP32 GPIO blink project"]


def test_fake_planner_returns_configured_plan_and_records_requirement() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )
    plan = ExecutionPlan(
        steps=[
            PlanStep(
                kind="create_project",
                description="Create the ESP-IDF project",
            ),
            PlanStep(
                kind="build_project",
                description="Build the ESP-IDF project",
            ),
        ]
    )
    planner = FakePlanner(plan)

    result = planner.create_plan(requirement)

    assert result is plan
    assert planner.calls == [requirement]


def test_fake_espidf_returns_evidence_in_configured_order() -> None:
    failed = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=1,
        error_category="source",
    )
    succeeded = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )
    espidf = FakeEspIdf([failed, succeeded])
    project_path = Path("workspace/blink")

    first_result = espidf.build(project_path)
    second_result = espidf.build(project_path)

    assert first_result is failed
    assert second_result is succeeded
    assert espidf.calls == [project_path, project_path]


def test_fake_espidf_rejects_unconfigured_extra_build() -> None:
    evidence = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )
    espidf = FakeEspIdf([evidence])
    project_path = Path("workspace/blink")

    espidf.build(project_path)

    with pytest.raises(
        RuntimeError,
        match="no configured build evidence remaining",
    ):
        espidf.build(project_path)


def test_fake_repair_planner_returns_configured_plan_and_records_inputs() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )
    plan = ExecutionPlan(
        steps=[PlanStep(kind="build_project", description="Build project")]
    )
    evidence = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=1,
        error_category="source",
    )
    files = [ProjectFile(path="main/main.c", content="broken source")]
    repair = RepairPlan(
        diagnosis="fix the source error",
        replacements=[
            FileReplacement(path="main/main.c", content="fixed source")
        ],
    )
    planner = FakeRepairPlanner(repair)

    result = planner.create_repair(requirement, plan, evidence, files)
    files.append(ProjectFile(path="main/extra.c", content="later mutation"))

    assert result is repair
    assert planner.calls == [
        (
            requirement,
            plan,
            evidence,
            [ProjectFile(path="main/main.c", content="broken source")],
        )
    ]


def test_fake_workspace_records_reads_and_applied_repairs() -> None:
    project_path = Path("workspace/firmware")
    files = [ProjectFile(path="main/main.c", content="broken source")]
    repair = RepairPlan(
        diagnosis="fix the source error",
        replacements=[
            FileReplacement(path="main/main.c", content="fixed source")
        ],
    )
    workspace = FakeWorkspace(files)

    returned_files = workspace.read_project_files(project_path)
    changed_files = workspace.apply_repair(project_path, repair)

    assert returned_files == files
    assert returned_files is not workspace.files
    assert changed_files == ["main/main.c"]
    assert workspace.read_calls == [project_path]
    assert workspace.apply_calls == [(project_path, repair)]


def test_fake_project_creator_returns_configured_evidence_in_order() -> None:
    from luxar.adapters.fake_project_creator import FakeProjectCreator
    from luxar.domain.projects import ProjectEvidence

    first = ProjectEvidence(
        success=False,
        command=["idf.py", "create-project", "blink"],
        return_code=1,
        error_category="environment",
    )
    second = ProjectEvidence(
        success=True,
        command=["idf.py", "create-project", "blink"],
        return_code=0,
        created_dir="blink",
    )
    creator = FakeProjectCreator([first, second])
    parent = Path("workspace")

    assert creator.create_project(parent, "blink", "esp32") is first
    assert creator.create_project(parent, "blink", "esp32") is second
    assert creator.calls == [
        (parent, "blink", "esp32"),
        (parent, "blink", "esp32"),
    ]

    with pytest.raises(RuntimeError, match="no configured evidence"):
        creator.create_project(parent, "blink", "esp32")
