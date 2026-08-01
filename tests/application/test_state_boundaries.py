from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.application.context import RuntimeContext
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.requirements import FirmwareRequirement


def test_runtime_context_keeps_dependencies_outside_workflow_state() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    plan = ExecutionPlan(
        steps=[
            PlanStep(
                kind="build_project",
                description="Build the ESP-IDF project",
            )
        ]
    )
    parser = FakeRequirementParser(requirement)
    planner = FakePlanner(plan)
    espidf = FakeEspIdf(
        [
            BuildEvidence(
                success=True,
                command=["idf.py", "build"],
                return_code=0,
            )
        ]
    )
    context = RuntimeContext(
        requirement_parser=parser,
        planner=planner,
        espidf=espidf,
        project_path=Path("workspace/blink"),
    )

    assert context.requirement_parser is parser
    assert context.planner is planner
    assert context.espidf is espidf

    with pytest.raises(FrozenInstanceError):
        context.project_path = Path("workspace/other")  # type: ignore[misc]
