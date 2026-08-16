from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_flasher import FakeFlasher
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_project_creator import FakeProjectCreator
from luxar.adapters.fake_repair_planner import FakeRepairPlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.application.context import RuntimeContext
from luxar.application.nodes import (
    analyze_requirement,
    build_project,
    completed,
    create_plan,
    create_project,
    execute_next_step,
    failed,
    repair_project,
    request_clarification,
)
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.projects import ProjectEvidence
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
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        serial_port=None,
        checkpointer=InMemorySaver(),
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
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        serial_port=None,
        checkpointer=InMemorySaver(),
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
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        serial_port=None,
        checkpointer=InMemorySaver(),
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
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        serial_port=None,
        checkpointer=InMemorySaver(),
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


def make_multi_step_plan() -> ExecutionPlan:
    return ExecutionPlan(
        steps=[
            PlanStep(kind="create_project", description="Create project"),
            PlanStep(kind="build_project", description="Build project"),
            PlanStep(kind="flash_project", description="Flash project"),
        ]
    )


def test_execute_next_step_dispatches_first_step_and_advances_cursor() -> None:
    state = {
        "plan": ExecutionPlan(
            steps=[
                PlanStep(kind="build_project", description="Build"),
                PlanStep(kind="flash_project", description="Flash"),
            ]
        ),
        "plan_index": 0,
        "trace": ["analyze_requirement", "create_plan"],
    }

    update = execute_next_step(state)

    assert update == {
        "plan_index": 1,
        "pending_step_kind": "build_project",
        "trace": [
            "analyze_requirement",
            "create_plan",
            "execute_next_step",
        ],
    }


def test_execute_next_step_uses_zero_when_cursor_missing() -> None:
    update = execute_next_step(
        {
            "plan": ExecutionPlan(
                steps=[
                    PlanStep(kind="build_project", description="Build"),
                ]
            ),
            "trace": [],
        }
    )

    assert update["plan_index"] == 1
    assert update["pending_step_kind"] == "build_project"
    assert "error" not in update


def test_execute_next_step_reports_completion_when_plan_exhausted() -> None:
    plan = make_multi_step_plan()
    state = {
        "plan": plan,
        "plan_index": len(plan.steps),
        "trace": [],
    }

    update = execute_next_step(state)

    assert update == {
        "pending_step_kind": None,
        "trace": ["execute_next_step"],
    }


def test_execute_next_step_supports_build_steps_in_s1() -> None:
    state = {
        "plan": ExecutionPlan(
            steps=[
                PlanStep(kind="build_project", description="Build"),
            ]
        ),
        "plan_index": 0,
        "trace": [],
    }

    update = execute_next_step(state)

    assert update["pending_step_kind"] == "build_project"
    assert "error" not in update


def test_execute_next_step_rejects_not_yet_supported_step_with_fixed_error() -> None:
    # S3 实现了 create/build/flash；监控步骤在 S4 之前必须给出固定失败。
    state = {
        "plan": ExecutionPlan(
            steps=[
                PlanStep(kind="build_project", description="Build"),
                PlanStep(kind="flash_project", description="Flash"),
                PlanStep(kind="monitor_project", description="Monitor"),
            ]
        ),
        "plan_index": 2,
        "trace": [],
    }

    update = execute_next_step(state)

    assert update["pending_step_kind"] == "monitor_project"
    error = update["error"]
    assert error.stage == "planning"
    assert error.category == "model_output"
    assert error.retryable is False
    assert "monitor_project" in error.message
    assert error.user_suggestion


def create_evidence() -> ProjectEvidence:
    return ProjectEvidence(
        success=True,
        command=["idf.py", "create-project", "blink"],
        return_code=0,
        created_dir="blink",
    )


def test_create_project_uses_context_creator_and_explicit_target() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    plan = ExecutionPlan(
        steps=[PlanStep(kind="build_project", description="Build project")]
    )
    creator = FakeProjectCreator([create_evidence()])
    project_path = Path("workspace/blink")
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        espidf=FakeEspIdf([]),
        project_path=project_path,
        repair_planner=configured_repair_planner(),
        workspace=FakeWorkspace([]),
        project_creator=creator,
        target_chip="esp32s3",
        flasher=FakeFlasher([]),
        serial_port=None,
        checkpointer=InMemorySaver(),
    )

    update = create_project(
        {"requirement": requirement, "trace": ["analyze_requirement", "create_plan"]},
        Runtime(context=context),
    )

    assert update == {
        "created_project": create_evidence(),
        "status": "project_created",
        "trace": [
            "analyze_requirement",
            "create_plan",
            "create_project",
        ],
    }
    # 显式配置的芯片优先于模型输出。
    assert creator.calls == [
        (project_path.parent, "blink", "esp32s3")
    ]


def test_create_project_falls_back_to_requirement_target() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    plan = ExecutionPlan(
        steps=[PlanStep(kind="build_project", description="Build project")]
    )
    creator = FakeProjectCreator([create_evidence()])
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        espidf=FakeEspIdf([]),
        project_path=Path("workspace/blink"),
        repair_planner=configured_repair_planner(),
        workspace=FakeWorkspace([]),
        project_creator=creator,
        target_chip=None,
        flasher=FakeFlasher([]),
        serial_port=None,
        checkpointer=InMemorySaver(),
    )

    create_project(
        {"requirement": requirement, "trace": []},
        Runtime(context=context),
    )

    assert creator.calls == [
        (Path("workspace"), "blink", "esp32")
    ]
