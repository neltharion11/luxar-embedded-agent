from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_flasher import FakeFlasher
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_project_creator import FakeProjectCreator
from luxar.adapters.fake_repair_planner import FakeRepairPlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.application.context import RuntimeContext
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.repairs import FileReplacement, RepairPlan
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
    repair_planner = FakeRepairPlanner(
        RepairPlan(
            diagnosis="configured test repair",
            replacements=[
                FileReplacement(path="main/main.c", content="fixed source")
            ],
        )
    )
    workspace = FakeWorkspace([])
    project_creator = FakeProjectCreator([])
    context = RuntimeContext(
        requirement_parser=parser,
        planner=planner,
        espidf=espidf,
        project_path=Path("workspace/blink"),
        repair_planner=repair_planner,
        workspace=workspace,
        project_creator=project_creator,
        target_chip=None,
        flasher=FakeFlasher([]),
        serial_port=None,
        checkpointer=InMemorySaver(),
    )

    assert context.requirement_parser is parser
    assert context.planner is planner
    assert context.espidf is espidf
    assert context.repair_planner is repair_planner
    assert context.workspace is workspace
    assert context.project_creator is project_creator
    assert context.target_chip is None

    with pytest.raises(FrozenInstanceError):
        context.project_path = Path("workspace/other")  # type: ignore[misc]
