from pathlib import Path

import pytest

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
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
