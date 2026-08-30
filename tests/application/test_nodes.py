import hashlib
from dataclasses import replace
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_flasher import FakeFlasher
from luxar.adapters.fake_log_analyst import FakeLogAnalyst
from luxar.adapters.fake_monitor import FakeMonitor
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_project_creator import FakeProjectCreator
from luxar.adapters.fake_repair_planner import FakeRepairPlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.application.context import RuntimeContext
from luxar.application.nodes import (
    analyze_device_logs,
    analyze_requirement,
    build_project,
    completed,
    create_plan,
    create_project,
    execute_next_step,
    failed,
    monitor_project,
    repair_project,
    request_clarification,
)
from luxar.domain.devices import (
    DeviceDiagnosis,
    DeviceLogDiagnostic,
    MonitorEvidence,
)
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.projects import ProjectEvidence
from luxar.domain.project_analysis import ProjectAnalysis
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
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        monitor_timeout_seconds=10,
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

    parser.requirement = FirmwareRequirement(
        target="",
        feature="empty_project",
        missing_fields=["target"],
    )
    project_context = replace(context, target_chip="esp32s3")

    project_update = analyze_requirement(
        {"task_text": "搭建一个空项目", "trace": []},
        Runtime(context=project_context),
    )

    assert project_update["requirement"] == FirmwareRequirement(
        target="esp32s3",
        feature="empty_project",
        missing_fields=[],
    )


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
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        monitor_timeout_seconds=10,
    )

    update = create_plan(
        {
            "requirement": requirement,
            "project_analysis": ProjectAnalysis(
                project_exists=True,
                has_source_code=True,
                fingerprint="current",
                summary="blink source",
            ),
            "trace": ["analyze_requirement", "analyze_project"],
        },
        Runtime(context=context),
    )

    assert update == {
        "plan": plan,
        "status": "planned",
        "trace": ["analyze_requirement", "analyze_project", "create_plan"],
    }
    assert planner.calls == [requirement]
    assert planner.project_analyses == [
        ProjectAnalysis(
            project_exists=True,
            has_source_code=True,
            fingerprint="current",
            summary="blink source",
        )
    ]


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
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        monitor_timeout_seconds=10,
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
    # FakeWorkspace 构造时会自动填充 sha256；期望值保持一致，避免全等比较失败
    expected_files = [
        ProjectFile(
            path=item.path,
            content=item.content,
            sha256=hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
        )
        for item in files
    ]
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
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        monitor_timeout_seconds=10,
    )
    state = {
        "requirement": requirement,
        "plan": plan,
        "build_evidence": evidence,
        "attempts": 1,
        "trace": ["analyze_requirement", "create_plan", "build_project"],
    }

    update = repair_project(state, Runtime(context=context))

    assert workspace.read_calls == [project_path, project_path]
    assert repair_planner.calls == [
        (requirement, plan, evidence, expected_files, None)
    ]
    assert workspace.apply_calls == [(project_path, repair)]
    assert update == {
        "repair_plan": repair,
        "changed_files": ["main/main.c"],
        "project_analysis": update["project_analysis"],
        "repair_origin": "build",
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


def test_execute_next_step_rejects_unknown_step_with_fixed_error() -> None:
    # 领域验证器会挡住未知步骤；此处用 model_construct 绕过验证，
    # 证明分发器自身仍有第二道防御。
    plan = ExecutionPlan.model_construct(
        steps=[
            PlanStep.model_construct(
                kind="erase_flash",  # type: ignore[arg-type]
                description="未知动作",
            )
        ]
    )
    update = execute_next_step(
        {
            "plan": plan,
            "plan_index": 0,
            "trace": [],
        }
    )

    assert update["pending_step_kind"] == "erase_flash"
    error = update["error"]
    assert error.stage == "planning"
    assert error.category == "model_output"
    assert error.retryable is False
    assert "erase_flash" in error.message
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
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        monitor_timeout_seconds=10,
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
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        monitor_timeout_seconds=10,
    )

    create_project(
        {"requirement": requirement, "trace": []},
        Runtime(context=context),
    )

    assert creator.calls == [
        (Path("workspace"), "blink", "esp32")
    ]


def monitor_evidence() -> MonitorEvidence:
    return MonitorEvidence(
        command=["idf.py", "-p", "COM3", "monitor"],
        port="COM3",
        capture_timeout_seconds=10,
        captured_log="boot ok",
        terminated_by_timeout=True,
    )


def test_monitor_project_uses_context_port_and_window() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    plan = ExecutionPlan(
        steps=[PlanStep(kind="build_project", description="Build project")]
    )
    monitor = FakeMonitor([monitor_evidence()])
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        espidf=FakeEspIdf([]),
        project_path=Path("workspace/blink"),
        repair_planner=configured_repair_planner(),
        workspace=FakeWorkspace([]),
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        monitor=monitor,
        log_analyst=FakeLogAnalyst([]),
        serial_port="COM3",
        monitor_timeout_seconds=25,
        checkpointer=InMemorySaver(),
    )

    update = monitor_project(
        {"trace": ["analyze_requirement", "create_plan"]},
        Runtime(context=context),
    )

    assert update == {
        "monitor_evidence": monitor_evidence(),
        "status": "monitoring",
        "trace": [
            "analyze_requirement",
            "create_plan",
            "monitor_project",
        ],
    }
    assert monitor.calls == [
        (Path("workspace/blink"), "COM3", 25)
    ]


def test_analyze_device_logs_healthy_diagnosis() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    plan = ExecutionPlan(
        steps=[PlanStep(kind="build_project", description="Build project")]
    )
    diagnosis = DeviceDiagnosis(
        healthy=True,
        repair_needed=False,
        summary="运行正常",
    )
    analyst = FakeLogAnalyst([diagnosis])
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        espidf=FakeEspIdf([]),
        project_path=Path("workspace/blink"),
        repair_planner=configured_repair_planner(),
        workspace=FakeWorkspace([]),
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        monitor=FakeMonitor([]),
        log_analyst=analyst,
        serial_port=None,
        monitor_timeout_seconds=10,
        checkpointer=InMemorySaver(),
    )
    evidence = monitor_evidence()

    update = analyze_device_logs(
        {
            "requirement": requirement,
            "monitor_evidence": evidence,
            "trace": ["monitor_project"],
        },
        Runtime(context=context),
    )

    assert update == {
        "device_diagnosis": diagnosis,
        "device_cycles": 1,
        "status": "diagnosed",
        "trace": ["monitor_project", "analyze_device_logs"],
    }
    assert analyst.calls == [(requirement, evidence)]


def test_analyze_device_logs_budget_exhaustion_sets_fixed_error() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    plan = ExecutionPlan(
        steps=[PlanStep(kind="build_project", description="Build project")]
    )
    diagnosis = DeviceDiagnosis(
        healthy=False,
        repair_needed=True,
        summary="仍然崩溃",
    )
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        espidf=FakeEspIdf([]),
        project_path=Path("workspace/blink"),
        repair_planner=configured_repair_planner(),
        workspace=FakeWorkspace([]),
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([diagnosis]),
        serial_port=None,
        monitor_timeout_seconds=10,
        checkpointer=InMemorySaver(),
    )

    update = analyze_device_logs(
        {
            "requirement": requirement,
            "monitor_evidence": monitor_evidence(),
            "device_cycles": 3,
            "trace": [],
        },
        Runtime(context=context),
    )

    assert update["device_cycles"] == 4
    error = update["error"]
    assert error.stage == "monitor"
    assert error.category == "unknown"
    assert error.retryable is False
    assert "上限" in error.message


def test_analyze_device_logs_unhealthy_without_repair_sets_error() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    plan = ExecutionPlan(
        steps=[PlanStep(kind="build_project", description="Build project")]
    )
    diagnosis = DeviceDiagnosis(
        healthy=False,
        repair_needed=False,
        summary="疑似硬件故障",
    )
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        espidf=FakeEspIdf([]),
        project_path=Path("workspace/blink"),
        repair_planner=configured_repair_planner(),
        workspace=FakeWorkspace([]),
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([diagnosis]),
        serial_port=None,
        monitor_timeout_seconds=10,
        checkpointer=InMemorySaver(),
    )

    update = analyze_device_logs(
        {
            "requirement": requirement,
            "monitor_evidence": monitor_evidence(),
            "trace": [],
        },
        Runtime(context=context),
    )

    error = update["error"]
    assert error.stage == "monitor"
    assert "未发现可修复项" in error.message
