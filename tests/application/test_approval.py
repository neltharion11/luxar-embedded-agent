"""S3 审批流测试：锁定 langgraph interrupt() 的暂停/恢复语义。"""

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

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
from luxar.application.runner import resume_workflow, run_workflow
from luxar.application.state import WorkflowState
from luxar.domain.devices import ApprovalRequest, FlashEvidence
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.repairs import FileReplacement, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


def make_flash_plan() -> ExecutionPlan:
    return ExecutionPlan(
        steps=[
            PlanStep(kind="build_project", description="Build"),
            PlanStep(kind="flash_project", description="Flash"),
        ]
    )


def make_context(
    *,
    flasher: FakeFlasher,
    serial_port: str | None = "COM3",
    checkpointer: InMemorySaver | None = None,
) -> RuntimeContext:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )
    return RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(make_flash_plan()),
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
        repair_planner=FakeRepairPlanner(
            RepairPlan(
                diagnosis="unused",
                replacements=[
                    FileReplacement(path="main/main.c", content="x")
                ],
            )
        ),
        workspace=FakeWorkspace([]),
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=flasher,
        serial_port=serial_port,
        checkpointer=checkpointer or InMemorySaver(),
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        monitor_timeout_seconds=10,
    )


def make_initial_state() -> WorkflowState:
    return WorkflowState(
        task_text="build and flash GPIO blink",
        attempts=0,
        max_attempts=3,
        trace=[],
    )


def flash_success() -> FlashEvidence:
    return FlashEvidence(
        success=True,
        command=["idf.py", "-p", "COM3", "flash"],
        return_code=0,
        port="COM3",
    )


def test_run_pauses_and_returns_sanitized_approval_request() -> None:
    flasher = FakeFlasher([flash_success()])
    context = make_context(flasher=flasher)

    run_result = run_workflow(
        initial_state=make_initial_state(),
        context=context,
    )

    assert run_result.pending_approval == ApprovalRequest(
        project_name="blink",
        port="COM3",
        target_chip="esp32",
        summary="即将向串口设备烧录固件，请确认目标芯片与串口",
        step_description="flash_project",
        attempts=0,
    )
    assert run_result.state["approval_status"] == "pending"
    assert run_result.state["build_evidence"].success is True
    assert "flash_evidence" not in run_result.state
    # 审批通过前绝不触碰硬件。
    assert flasher.flash_calls == []


def test_resume_approved_completes_workflow() -> None:
    flasher = FakeFlasher([flash_success()])
    context = make_context(flasher=flasher)

    paused = run_workflow(
        initial_state=make_initial_state(),
        context=context,
    )

    resumed = resume_workflow(
        thread_id=paused.thread_id,
        context=context,
        approved=True,
    )

    assert resumed.pending_approval is None
    assert resumed.state["status"] == "completed"
    assert resumed.state["approval_status"] == "approved"
    assert resumed.state["flash_evidence"] == flash_success()
    assert resumed.state["trace"] == [
        "analyze_requirement",
        "create_plan",
        "execute_next_step",
        "build_project",
        "execute_next_step",
        "request_flash_approval",
        "flash_project",
        "execute_next_step",
        "completed",
    ]
    assert flasher.flash_calls == [(Path("workspace/blink"), "COM3")]


def test_resume_rejected_terminates_without_flashing() -> None:
    flasher = FakeFlasher([flash_success()])
    context = make_context(flasher=flasher)

    paused = run_workflow(
        initial_state=make_initial_state(),
        context=context,
    )

    resumed = resume_workflow(
        thread_id=paused.thread_id,
        context=context,
        approved=False,
    )

    assert resumed.state["status"] == "failed"
    assert resumed.state["approval_status"] == "rejected"
    assert resumed.state["error"].category == "approval_rejected"
    assert resumed.state["error"].stage == "flash"
    assert resumed.state["error"].retryable is False
    assert flasher.flash_calls == []


def test_approval_handler_approves_in_one_call() -> None:
    flasher = FakeFlasher([flash_success()])
    context = make_context(flasher=flasher)
    decisions: list[ApprovalRequest] = []

    run_result = run_workflow(
        initial_state=make_initial_state(),
        context=context,
        approval_handler=lambda request: (
            decisions.append(request) or True
        ),
    )

    assert run_result.state["status"] == "completed"
    assert len(decisions) == 1
    assert decisions[0].port == "COM3"
    assert flasher.flash_calls == [(Path("workspace/blink"), "COM3")]


def test_approval_handler_rejects() -> None:
    flasher = FakeFlasher([flash_success()])
    context = make_context(flasher=flasher)

    run_result = run_workflow(
        initial_state=make_initial_state(),
        context=context,
        approval_handler=lambda request: False,
    )

    assert run_result.state["status"] == "failed"
    assert run_result.state["error"].category == "approval_rejected"
    assert flasher.flash_calls == []


def test_flash_retry_does_not_request_approval_again() -> None:
    flasher = FakeFlasher(
        [
            FlashEvidence(
                success=False,
                command=["idf.py", "-p", "COM3", "flash"],
                return_code=1,
                port="COM3",
                error_category="serial",
            ),
            flash_success(),
        ]
    )
    context = make_context(flasher=flasher)
    decisions: list[ApprovalRequest] = []

    run_result = run_workflow(
        initial_state=make_initial_state(),
        context=context,
        approval_handler=lambda request: (
            decisions.append(request) or True
        ),
    )

    assert run_result.state["status"] == "completed"
    assert run_result.state["flash_attempts"] == 2
    assert len(decisions) == 1
    assert len(flasher.flash_calls) == 2


def test_missing_serial_port_fails_before_approval() -> None:
    flasher = FakeFlasher([flash_success()])
    context = make_context(flasher=flasher, serial_port=None)

    run_result = run_workflow(
        initial_state=make_initial_state(),
        context=context,
    )

    assert run_result.pending_approval is None
    assert run_result.state["status"] == "failed"
    assert run_result.state["error"].category == "serial"
    assert run_result.state["error"].stage == "flash"
    assert flasher.flash_calls == []
