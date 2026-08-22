"""S4 设备回路测试：监控 → 诊断 → 修复 → 重建 → 重烧 → 再监控 的完整闭环。"""

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
from luxar.application.runner import run_workflow
from luxar.application.state import WorkflowState
from luxar.domain.devices import (
    DeviceDiagnosis,
    FlashEvidence,
    MonitorEvidence,
)
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.repairs import FileReplacement, ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


def make_device_plan() -> ExecutionPlan:
    return ExecutionPlan(
        steps=[
            PlanStep(kind="build_project", description="Build"),
            PlanStep(kind="flash_project", description="Flash"),
            PlanStep(kind="monitor_project", description="Monitor"),
        ]
    )


def make_context(
    *,
    espidf: FakeEspIdf,
    flasher: FakeFlasher,
    monitor: FakeMonitor,
    log_analyst: FakeLogAnalyst,
    repair_planner: FakeRepairPlanner,
) -> RuntimeContext:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )
    return RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(make_device_plan()),
        espidf=espidf,
        project_path=Path("workspace/blink"),
        repair_planner=repair_planner,
        workspace=FakeWorkspace(
            [ProjectFile(path="main/main.c", content="broken source")]
        ),
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=flasher,
        monitor=monitor,
        log_analyst=log_analyst,
        serial_port="COM3",
        monitor_timeout_seconds=10,
        checkpointer=InMemorySaver(),
    )


def initial_state() -> WorkflowState:
    return WorkflowState(
        task_text="build flash and verify blink",
        attempts=0,
        max_attempts=3,
        trace=[],
    )


def build_ok() -> BuildEvidence:
    return BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )


def flash_ok() -> FlashEvidence:
    return FlashEvidence(
        success=True,
        command=["idf.py", "-p", "COM3", "flash"],
        return_code=0,
        port="COM3",
    )


def monitor_with(diagnostics_summary: str) -> MonitorEvidence:
    return MonitorEvidence(
        command=["idf.py", "-p", "COM3", "monitor"],
        port="COM3",
        capture_timeout_seconds=10,
        captured_log=diagnostics_summary,
        terminated_by_timeout=True,
    )


def repair_plan() -> RepairPlan:
    return RepairPlan(
        diagnosis="fix watchdog",
        replacements=[
            FileReplacement(path="main/main.c", content="fixed source")
        ],
    )


def test_device_loop_repairs_and_reflashes_until_healthy() -> None:
    # 第一次监控发现看门狗 → 修复重建重烧 → 第二次监控健康完成。
    monitor = FakeMonitor(
        [
            monitor_with("task_wdt timeout"),
            monitor_with("boot ok"),
        ]
    )
    analyst = FakeLogAnalyst(
        [
            DeviceDiagnosis(
                healthy=False,
                repair_needed=True,
                summary="看门狗超时",
                findings=["task_wdt"],
            ),
            DeviceDiagnosis(
                healthy=True,
                repair_needed=False,
                summary="运行正常",
            ),
        ]
    )
    repair_planner = FakeRepairPlanner(repair_plan())
    context = make_context(
        espidf=FakeEspIdf([build_ok(), build_ok()]),
        flasher=FakeFlasher([flash_ok(), flash_ok()]),
        monitor=monitor,
        log_analyst=analyst,
        repair_planner=repair_planner,
    )
    approvals = []

    run_result = run_workflow(
        initial_state=initial_state(),
        context=context,
        approval_handler=lambda request: (
            approvals.append(request) or True
        ),
    )

    assert run_result.state["status"] == "completed"
    assert run_result.state["device_cycles"] == 2
    assert run_result.state["device_diagnosis"].healthy is True
    # 审批只发生一次，设备回路重烧复用授权。
    assert len(approvals) == 1
    assert len(monitor.calls) == 2
    assert len(context.flasher.flash_calls) == 2
    # 设备回路修复把日志诊断传给了修复模型。
    assert repair_planner.calls[0][4] == DeviceDiagnosis(
        healthy=False,
        repair_needed=True,
        summary="看门狗超时",
        findings=["task_wdt"],
    )
    assert run_result.state["trace"] == [
            "analyze_requirement",
            "analyze_project",
            "create_plan",
        "execute_next_step",
        "build_project",
        "execute_next_step",
        "request_flash_approval",
        "flash_project",
        "execute_next_step",
        "monitor_project",
        "analyze_device_logs",
        "repair_project",
        "build_project",
        "request_flash_approval",
        "flash_project",
        "monitor_project",
        "analyze_device_logs",
        "completed",
    ]


def test_device_loop_budget_exhaustion_terminates() -> None:
    # 三次诊断都需要修复且从未健康 → 预算耗尽后终止。
    diagnosis = DeviceDiagnosis(
        healthy=False,
        repair_needed=True,
        summary="仍然崩溃",
    )
    context = make_context(
        espidf=FakeEspIdf([build_ok()] * 4),
        flasher=FakeFlasher([flash_ok()] * 4),
        monitor=FakeMonitor([monitor_with("panic")] * 4),
        log_analyst=FakeLogAnalyst([diagnosis] * 4),
        repair_planner=FakeRepairPlanner(repair_plan()),
    )

    run_result = run_workflow(
        initial_state=initial_state(),
        context=context,
        approval_handler=lambda request: True,
    )

    assert run_result.state["status"] == "failed"
    assert run_result.state["device_cycles"] == 4
    assert run_result.state["error"].stage == "monitor"
    assert "上限" in run_result.state["error"].message
    # 预算内最多三次修复循环：3 次修复 + 初始构建与烧录。
    assert len(context.flasher.flash_calls) == 4


def test_device_loop_healthy_on_first_monitor_completes_directly() -> None:
    context = make_context(
        espidf=FakeEspIdf([build_ok()]),
        flasher=FakeFlasher([flash_ok()]),
        monitor=FakeMonitor([monitor_with("boot ok")]),
        log_analyst=FakeLogAnalyst(
            [
                DeviceDiagnosis(
                    healthy=True,
                    repair_needed=False,
                    summary="运行正常",
                )
            ]
        ),
        repair_planner=FakeRepairPlanner(repair_plan()),
    )

    run_result = run_workflow(
        initial_state=initial_state(),
        context=context,
        approval_handler=lambda request: True,
    )

    assert run_result.state["status"] == "completed"
    assert run_result.state["device_cycles"] == 1
    assert "repair_plan" not in run_result.state
    assert context.flasher.flash_calls == [(Path("workspace/blink"), "COM3")]
