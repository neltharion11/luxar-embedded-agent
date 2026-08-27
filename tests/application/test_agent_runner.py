from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from luxar.adapters.fake_flasher import FakeFlasher
from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.application.agent_runner import run_agent_workflow
from luxar.application.agent_runner import resume_agent_workflow
from luxar.application.agent_state import AgentRuntimeContext
from luxar.database import TransientPersistence
from luxar.domain.agent.changes import CapabilityChange, ChangeSet
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.agent.tasks import build_task_graph
from luxar.domain.agent.verification import VerificationPlan
from luxar.domain.devices import FlashEvidence, MonitorEvidence
from luxar.domain.evidence import BuildEvidence
from luxar.ports.workspace_errors import WorkspaceError


GPIO_SOURCE = """
#include "driver/gpio.h"
void app_main(void) {
    gpio_set_direction(GPIO_NUM_13, GPIO_MODE_OUTPUT);
    gpio_set_level(GPIO_NUM_13, 1);
}
"""


def _write_project(root: Path) -> None:
    main = root / "main"
    main.mkdir()
    (main / "main.c").write_text(GPIO_SOURCE, encoding="utf-8")


def test_agent_runner_reads_workspace_and_reports_progress(tmp_path: Path) -> None:
    _write_project(tmp_path)
    progress = []

    result = run_agent_workflow(
        initial_state={
            "task_text": "GPIO 输出模式是什么？",
            "source_message_id": "runner-question",
        },
        context=AgentRuntimeContext(
            workspace=LocalWorkspaceAdapter(),
            project_path=tmp_path,
        ),
        checkpointer=InMemorySaver(),
        thread_id="agent-runner-question",
        progress_reporter=progress.append,
    )

    assert result.thread_id == "agent-runner-question"
    assert result.state["status"] == "awaiting_user"
    assert result.state["project_name"] == tmp_path.name
    assert result.state["project_files"][0].path == "main/main.c"
    assert "gpio.output:P13" in {
        capability.capability_id
        for capability in result.state["capabilities"]
    }
    assert [item.node for item in progress] == [
        "load_project_session",
        "supervisor",
        "project_inspector",
        "supervisor",
        "answer_user",
    ]
    assert [item.phase for item in progress] == [
        "completed",
        "decision",
        "completed",
        "decision",
        "completed",
    ]
    assert progress[1].tools == (
        "workspace.read_project_files",
        "project.inspect",
    )
    assert "Supervisor 决策" in progress[1].narrative
    assert "准备调用：" in progress[1].narrative
    assert "读取工程文件" in progress[1].narrative
    assert "本步调用完成：" in progress[2].narrative
    assert "识别出" in progress[2].narrative
    assert all(item.narrative for item in progress)


def test_agent_runner_persists_blocked_project_snapshot(tmp_path: Path) -> None:
    _write_project(tmp_path)
    persistence = TransientPersistence()

    result = run_agent_workflow(
        initial_state={
            "task_text": "新增 P33 高电平",
            "source_message_id": "runner-persistence",
        },
        context=AgentRuntimeContext(
            workspace=LocalWorkspaceAdapter(),
            project_path=tmp_path,
        ),
        persistence=persistence,
        project_key="0:runner-project",
    )

    assert result.state["status"] == "blocked"
    record = persistence.get_agent_project("0:runner-project")
    assert record is not None
    assert record.objective["objective_id"]
    assert record.snapshot["status"] == "blocked"


def test_agent_runner_sanitizes_workspace_failure(tmp_path: Path) -> None:
    class BrokenWorkspace:
        def read_project_files(self, project_path):
            raise WorkspaceError(
                category="io",
                message=f"secret path: {project_path}",
                retryable=False,
            )

    result = run_agent_workflow(
        initial_state={"task_text": "检查工程"},
        context=AgentRuntimeContext(
            workspace=BrokenWorkspace(),
            project_path=tmp_path,
        ),
    )

    assert result.state["status"] == "failed"
    assert result.state["last_error"] == "工程源码读取或受控写入失败"
    assert str(tmp_path) not in result.state["last_error"]


def _approval_initial_state() -> dict[str, object]:
    objective = ProjectObjective(
        objective_id="runner-approval",
        title="审批受控架构任务",
        description="验证 Supervisor 审批暂停和恢复",
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="preserve",
                capability_id="project.baseline",
                rationale="审批测试不修改源码",
            )
        ]
    )
    model = ProjectModelExtractor().extract([])
    graph = build_task_graph(
        objective,
        change_set,
        verification_plan=VerificationPlan(require_build=False),
    )
    inspect = next(task for task in graph.tasks if task.kind == "inspect_project")
    architecture = next(
        task for task in graph.tasks if task.kind == "architecture_plan"
    )
    graph = graph.update_task(inspect.task_id, status="passed")
    graph = graph.update_task(architecture.task_id, requires_approval=True)
    return {
        "objective": objective,
        "change_set": change_set,
        "project_files": [],
        "project_model": model,
        "hardware_report": model.hardware_report,
        "inspection_complete": True,
        "hardware_validated": True,
        "task_graph": graph,
        "verification_plan": VerificationPlan(require_build=False),
        "trace": [],
    }


def test_agent_runner_pauses_and_resumes_approved_task() -> None:
    persistence = TransientPersistence()
    paused = run_agent_workflow(
        initial_state=_approval_initial_state(),
        context=AgentRuntimeContext(),
        persistence=persistence,
        project_key="0:approval",
        thread_id="agent-approval-approved",
    )

    assert paused.pending_approval is not None
    assert paused.pending_approval.task_id.endswith(":architecture")
    assert paused.state["status"] == "awaiting_user"
    assert paused.state["approval_status"] == "pending"
    assert paused.checkpointer is not None

    resumed = resume_agent_workflow(
        thread_id=paused.thread_id,
        context=AgentRuntimeContext(),
        checkpointer=paused.checkpointer,
        approved=True,
        persistence=persistence,
        project_key="0:approval",
    )

    assert resumed.pending_approval is None
    assert resumed.state["status"] == "completed"
    assert resumed.state["approval_status"] == "approved"
    assert any(
        evidence.startswith("approval:runner-approval:architecture")
        for evidence in resumed.state["evidence_ids"]
    )
    record = persistence.get_agent_project("0:approval")
    assert record is not None
    assert record.snapshot["status"] == "completed"


def test_agent_runner_rejection_blocks_task_without_execution() -> None:
    paused = run_agent_workflow(
        initial_state=_approval_initial_state(),
        context=AgentRuntimeContext(),
        thread_id="agent-approval-rejected",
    )
    assert paused.pending_approval is not None
    assert paused.checkpointer is not None

    resumed = resume_agent_workflow(
        thread_id=paused.thread_id,
        context=AgentRuntimeContext(),
        checkpointer=paused.checkpointer,
        approved=False,
        feedback="暂不执行",
    )

    assert resumed.state["status"] == "blocked"
    assert resumed.state["approval_status"] == "rejected"
    assert resumed.state["last_error"] == "用户拒绝高风险任务审批"
    assert "暂不执行" in resumed.state["task_feedback"][
        "runner-approval:architecture"
    ]


def test_agent_flash_verification_requires_approval_before_device_write(
    tmp_path: Path,
) -> None:
    class Builder:
        calls = 0

        def build(self, project_path: Path) -> BuildEvidence:
            assert project_path == tmp_path
            self.calls += 1
            return BuildEvidence(
                success=True,
                command=["idf.py", "build"],
                return_code=0,
            )

    objective = ProjectObjective(
        objective_id="runner-flash",
        title="构建并烧录",
        description="构建通过后烧录设备",
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="preserve",
                capability_id="project.baseline",
            )
        ]
    )
    plan = VerificationPlan(require_build=True, require_flash=True)
    model = ProjectModelExtractor().extract([])
    graph = build_task_graph(objective, change_set, verification_plan=plan)
    builder = Builder()
    flasher = FakeFlasher(
        [
            FlashEvidence(
                success=True,
                command=["idf.py", "-p", "COM3", "flash"],
                return_code=0,
                port="COM3",
            )
        ]
    )
    context = AgentRuntimeContext(
        project_path=tmp_path,
        build_executor=builder,
        flasher=flasher,
        serial_port="COM3",
    )
    paused = run_agent_workflow(
        initial_state={
            "objective": objective,
            "change_set": change_set,
            "project_files": [],
            "project_model": model,
            "hardware_report": model.hardware_report,
            "inspection_complete": True,
            "hardware_validated": True,
            "task_graph": graph,
            "verification_plan": plan,
            "trace": [],
        },
        context=context,
        thread_id="agent-flash-approved",
    )

    assert paused.pending_approval is not None
    assert paused.pending_approval.operation == "device.flash"
    assert "包含设备烧录" in paused.pending_approval.summary
    assert paused.pending_approval.task_description
    assert "espidf.build" in paused.pending_approval.tools
    assert "device.flash" in paused.pending_approval.tools
    assert "执行 ESP-IDF 构建" in paused.pending_approval.planned_actions
    assert "将构建产物烧录到所选开发板" in (
        paused.pending_approval.planned_actions
    )
    assert "开发板串口 COM3" in paused.pending_approval.affected_targets
    assert paused.pending_approval.acceptance_criteria
    assert any("覆盖所选开发板当前固件" in risk for risk in paused.pending_approval.risks)
    assert builder.calls == 0
    assert flasher.flash_calls == []
    assert paused.checkpointer is not None

    resumed = resume_agent_workflow(
        thread_id=paused.thread_id,
        context=context,
        checkpointer=paused.checkpointer,
        approved=True,
    )

    assert resumed.state["status"] == "completed"
    assert resumed.state["build_verified"] is True
    assert resumed.state["flash_evidence"].success is True
    assert builder.calls == 1
    assert flasher.flash_calls == [(tmp_path, "COM3")]
    assert "flash:runner-flash:verify" in resumed.state["evidence_ids"]
    assert "approval:runner-flash:verify" in resumed.state["evidence_ids"]


def test_agent_flash_uses_combined_flash_monitor_evidence(
    tmp_path: Path,
) -> None:
    class CoordinatedFlasher(FakeFlasher):
        def __init__(self) -> None:
            super().__init__(
                [
                    FlashEvidence(
                        success=True,
                        command=["idf.py", "-p", "COM3", "flash"],
                        return_code=0,
                        port="COM3",
                    )
                ]
            )
            self.combined_calls: list[tuple[Path, str, int]] = []

        def flash_and_monitor(
            self,
            project_path: Path,
            port: str,
            timeout_seconds: int,
        ) -> tuple[FlashEvidence, MonitorEvidence]:
            self.combined_calls.append((project_path, port, timeout_seconds))
            return (
                FlashEvidence(
                    success=True,
                    command=["idf.py", "-p", port, "flash"],
                    return_code=0,
                    port=port,
                ),
                MonitorEvidence(
                    command=["idf.py", "-p", port, "flash", "monitor"],
                    port=port,
                    capture_timeout_seconds=timeout_seconds,
                    captured_log="boot ok",
                    terminated_by_timeout=True,
                ),
            )

    objective = ProjectObjective(
        objective_id="runner-flash-monitor",
        title="构建、烧录并检查设备",
        description="构建后烧录并检查设备日志",
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="preserve",
                capability_id="project.baseline",
            )
        ]
    )
    plan = VerificationPlan(
        require_build=True,
        require_flash=True,
        require_device=True,
    )
    model = ProjectModelExtractor().extract([])
    graph = build_task_graph(objective, change_set, verification_plan=plan)
    flasher = CoordinatedFlasher()

    class Builder:
        def build(self, path: Path) -> BuildEvidence:
            assert path == tmp_path
            return BuildEvidence(
                success=True,
                command=["idf.py", "build"],
                return_code=0,
            )

    context = AgentRuntimeContext(
        project_path=tmp_path,
        build_executor=Builder(),
        flasher=flasher,
        serial_port="COM3",
        monitor_timeout_seconds=7,
    )

    paused = run_agent_workflow(
        initial_state={
            "objective": objective,
            "change_set": change_set,
            "project_files": [],
            "project_model": model,
            "hardware_report": model.hardware_report,
            "inspection_complete": True,
            "hardware_validated": True,
            "task_graph": graph,
            "verification_plan": plan,
            "trace": [],
        },
        context=context,
        thread_id="agent-flash-monitor",
    )
    assert paused.pending_approval is not None

    resumed = resume_agent_workflow(
        thread_id=paused.thread_id,
        context=context,
        checkpointer=paused.checkpointer,
        approved=True,
    )

    assert resumed.state["status"] == "completed"
    assert resumed.state["hardware_function_verified"] is True
    assert resumed.state["monitor_evidence"].captured_log == "boot ok"
    assert flasher.combined_calls == [(tmp_path, "COM3", 7)]


def test_bare_flash_command_builds_flash_only_task_graph_before_approval(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path)

    result = run_agent_workflow(
        initial_state={
            "task_text": "烧录",
            "source_message_id": "bare-flash",
        },
        context=AgentRuntimeContext(
            workspace=LocalWorkspaceAdapter(),
            project_path=tmp_path,
            serial_port="COM3",
        ),
        thread_id="agent-bare-flash",
    )

    assert result.state["status"] == "awaiting_user"
    assert result.pending_approval is not None
    assert result.pending_approval.operation == "device.flash"
    assert result.state["objective"].title == "烧录当前工程固件"
    assert [task.kind for task in result.state["task_graph"].tasks] == [
        "inspect_project",
        "architecture_plan",
        "verify_acceptance",
    ]
    assert "项目规划模型未能生成有效变更" not in result.state.get(
        "last_error", ""
    )
