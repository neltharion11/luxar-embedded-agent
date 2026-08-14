from pathlib import Path

from langgraph.runtime import Runtime

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_repair_planner import FakeRepairPlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.application.context import RuntimeContext
from luxar.application.nodes import (
    analyze_requirement,
    build_project,
    completed,
    create_plan,
    failed,
    repair_project,
    request_clarification,
)
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.repairs import FileReplacement, ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


def configured_repair_planner() -> FakeRepairPlanner:
    return FakeRepairPlanner(
        RepairPlan(
            diagnosis="configured test repair",
            replacements=[
                FileReplacement(path="main/main.c", content="fixed source")
            ],
        )
    )


def test_analyze_requirement_uses_runtime_parser_and_updates_state() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )
    parser = FakeRequirementParser(requirement)
    context = RuntimeContext(
        requirement_parser=parser,
        planner=FakePlanner(
            ExecutionPlan(
                steps=[
                    PlanStep(
                        kind="build_project",
                        description="Build the project",
                    )
                ]
            )
        ),
        espidf=FakeEspIdf(
            [
                BuildEvidence(
                    success=True,
                    command=["idf.py", "build"],
                    return_code=0,
                )
            ]
        ),
        project_path=Path("workspace/blink"),
        repair_planner=configured_repair_planner(),
        workspace=FakeWorkspace([]),
    )
    runtime = Runtime(context=context)
    state = {
        "task_text": "create an ESP32 GPIO blink project",
        "trace": [],
    }

    update = analyze_requirement(state, runtime)

    assert update == {
        "requirement": requirement,
        "status": "requirement_analyzed",
        "trace": ["analyze_requirement"],
    }
    assert parser.calls == ["create an ESP32 GPIO blink project"]
    assert state["trace"] == []


def test_create_plan_passes_structured_requirement_to_runtime_planner() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )
    plan = ExecutionPlan(
        steps=[
            PlanStep(
                kind="build_project",
                description="Build the project",
            )
        ]
    )
    planner = FakePlanner(plan)
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=planner,
        espidf=FakeEspIdf(
            [
                BuildEvidence(
                    success=True,
                    command=["idf.py", "build"],
                    return_code=0,
                )
            ]
        ),
        project_path=Path("workspace/blink"),
        repair_planner=configured_repair_planner(),
        workspace=FakeWorkspace([]),
    )

    update = create_plan(
        {"requirement": requirement, "trace": ["analyze_requirement"]},
        Runtime(context=context),
    )

    assert update == {
        "plan": plan,
        "status": "planned",
        "trace": ["analyze_requirement", "create_plan"],
    }
    assert planner.calls == [requirement]


def test_build_project_records_tool_evidence_attempt_and_path() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    plan = ExecutionPlan(
        steps=[
            PlanStep(
                kind="build_project",
                description="Build the project",
            )
        ]
    )
    evidence = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )
    espidf = FakeEspIdf([evidence])
    project_path = Path("workspace/blink")
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        espidf=espidf,
        project_path=project_path,
        repair_planner=configured_repair_planner(),
        workspace=FakeWorkspace([]),
    )

    update = build_project(
        {
            "plan": plan,
            "attempts": 0,
            "trace": ["analyze_requirement", "create_plan"],
        },
        Runtime(context=context),
    )

    assert update == {
        "build_evidence": evidence,
        "attempts": 1,
        "status": "building",
        "trace": ["analyze_requirement", "create_plan", "build_project"],
    }
    assert espidf.calls == [project_path]


def test_request_clarification_marks_workflow_as_waiting_for_user() -> None:
    state = {"trace": ["analyze_requirement"]}

    update = request_clarification(state)

    assert update == {
        "status": "needs_clarification",
        "trace": ["analyze_requirement", "request_clarification"],
    }


def test_completed_marks_workflow_as_completed() -> None:
    state = {"trace": ["analyze_requirement", "create_plan", "build_project"]}

    update = completed(state)

    assert update == {
        "status": "completed",
        "trace": [
            "analyze_requirement",
            "create_plan",
            "build_project",
            "completed",
        ],
    }


def test_failed_marks_workflow_as_failed() -> None:
    state = {"trace": ["analyze_requirement", "create_plan", "build_project"]}

    update = failed(state)

    assert update == {
        "status": "failed",
        "trace": [
            "analyze_requirement",
            "create_plan",
            "build_project",
            "failed",
        ],
    }


def test_repair_project_reads_plans_and_applies_repair_without_build_attempt() -> None:
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
        stderr_summary="main/main.c:42: gpio_num undeclared",
        error_category="source",
    )
    files = [ProjectFile(path="main/main.c", content="broken source")]
    repair = RepairPlan(
        diagnosis="declare the GPIO variable",
        replacements=[
            FileReplacement(path="main/main.c", content="fixed source")
        ],
    )
    repair_planner = FakeRepairPlanner(repair)
    workspace = FakeWorkspace(files)
    project_path = Path("workspace/blink")
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        espidf=FakeEspIdf([]),
        project_path=project_path,
        repair_planner=repair_planner,
        workspace=workspace,
    )
    state = {
        "requirement": requirement,
        "plan": plan,
        "build_evidence": evidence,
        "attempts": 1,
        "trace": ["analyze_requirement", "create_plan", "build_project"],
    }

    update = repair_project(state, Runtime(context=context))

    assert workspace.read_calls == [project_path]
    assert repair_planner.calls == [(requirement, plan, evidence, files)]
    assert workspace.apply_calls == [(project_path, repair)]
    assert update == {
        "repair_plan": repair,
        "changed_files": ["main/main.c"],
        "status": "repaired",
        "trace": [
            "analyze_requirement",
            "create_plan",
            "build_project",
            "repair_project",
        ],
    }
    assert state["attempts"] == 1
    assert state["build_evidence"] is evidence
