from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from luxar.adapters.project_change_workflow import ProjectChangeWorkflow
from luxar.application.agent_runner import AgentWorkflowRunResult
from luxar.application.agent_runner import AgentWorkflowProgress
from luxar.application.agent_state import AgentRuntimeContext
from luxar.database import TransientPersistence
from luxar.domain.agent.approvals import AgentApprovalRequest
from luxar.domain.continuous_agent.steps import DomainWorkflowCall
from luxar.ports.domain_workflow import DomainWorkflowExecutionContext


def _call() -> DomainWorkflowCall:
    return DomainWorkflowCall(
        call_id="change-oled",
        workflow_name="project.change",
        task="修复 OLED 初始化并保留 TWAI",
    )


def _context(project: Path) -> DomainWorkflowExecutionContext:
    return DomainWorkflowExecutionContext(
        session_id="session-1",
        turn_id="turn-1",
        project_key="0:test4",
        project_path=project,
    )


def test_project_change_workflow_maps_nested_approval_and_resumes_same_thread(
    tmp_path: Path,
) -> None:
    child_threads: list[str] = []

    def runner(**kwargs: object) -> AgentWorkflowRunResult:
        child_threads.append(str(kwargs["thread_id"]))
        return AgentWorkflowRunResult(
            state={
                "task_text": _call().task,
                "status": "running",
                "trace": [],
            },
            thread_id=str(kwargs["thread_id"]),
            pending_approval=AgentApprovalRequest(
                task_id="edit-main",
                title="审批代码变更",
                summary="准备修改 main/main.c",
                operation="workspace.patch",
                risks=["会修改项目源码"],
                planned_actions=["事务式应用代码变更", "构建并检查验收条件"],
            ),
        )

    def resumer(**kwargs: object) -> AgentWorkflowRunResult:
        child_threads.append(str(kwargs["thread_id"]))
        assert kwargs["approved"] is True
        assert kwargs["feedback"] == "批准变更"
        return AgentWorkflowRunResult(
            state={
                "task_text": _call().task,
                "status": "completed",
                "trace": [],
                "acceptance_passed": True,
                "build_verified": True,
                "evidence_ids": ["build:latest"],
            },
            thread_id=str(kwargs["thread_id"]),
        )

    workflow = ProjectChangeWorkflow(
        runtime_context=AgentRuntimeContext(project_path=tmp_path),
        checkpointer=InMemorySaver(),
        persistence=TransientPersistence(),
        runner=runner,
        resumer=resumer,
    )

    waiting = workflow.start(_call(), _context(tmp_path))
    completed = workflow.resume(
        _call(),
        _context(tmp_path),
        approved=True,
        feedback="批准变更",
    )

    assert waiting.status == "waiting_approval"
    assert waiting.pending_approval is not None
    assert waiting.pending_approval.call_id == "change-oled"
    assert waiting.pending_approval.risk == "write"
    assert completed.status == "completed"
    assert completed.result["acceptance_passed"] is True
    assert child_threads[0] == child_threads[1]


def test_project_change_workflow_returns_blocked_to_top_agent() -> None:
    def runner(**kwargs: object) -> AgentWorkflowRunResult:
        return AgentWorkflowRunResult(
            state={
                "task_text": _call().task,
                "status": "blocked",
                "last_error": "构建环境缺失",
                "trace": [],
            },
            thread_id=str(kwargs["thread_id"]),
        )

    workflow = ProjectChangeWorkflow(
        runtime_context=AgentRuntimeContext(),
        checkpointer=InMemorySaver(),
        runner=runner,
    )

    outcome = workflow.start(_call(), _context(Path("test4")))

    assert outcome.status == "blocked"
    assert "构建环境缺失" in outcome.summary
    assert outcome.pending_approval is None


def test_project_change_workflow_forwards_supervisor_progress_and_real_tools() -> None:
    reported: list[tuple[str, dict[str, object]]] = []

    def runner(**kwargs: object) -> AgentWorkflowRunResult:
        progress_reporter = kwargs["progress_reporter"]
        assert callable(progress_reporter)
        progress_reporter(
            AgentWorkflowProgress(
                node="task_executor",
                message="正在构建并验证工程",
                step_count=4,
                phase="running",
                tools=("espidf.build", "source.assert"),
                task_id="verify-build",
                detail="等待 ESP-IDF 返回",
            )
        )
        return AgentWorkflowRunResult(
            state={
                "task_text": _call().task,
                "status": "completed",
                "trace": [],
                "acceptance_passed": True,
                "build_verified": True,
            },
            thread_id=str(kwargs["thread_id"]),
        )

    workflow = ProjectChangeWorkflow(
        runtime_context=AgentRuntimeContext(),
        checkpointer=InMemorySaver(),
        runner=runner,
    )
    context = DomainWorkflowExecutionContext(
        session_id="session-1",
        turn_id="turn-1",
        project_key="0:test4",
        project_path=Path("test4"),
        event_reporter=lambda event, data: reported.append((event, data)),
    )

    workflow.start(_call(), context)

    assert reported == [
        (
            "progress",
            {
                "stage": "task_executor",
                "message": "正在构建并验证工程",
                "attempts": 4,
                "phase": "running",
                "tools": ["espidf.build", "source.assert"],
                "task_id": "verify-build",
                "detail": "等待 ESP-IDF 返回",
            },
        )
    ]
