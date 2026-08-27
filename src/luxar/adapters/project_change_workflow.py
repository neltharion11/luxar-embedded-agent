"""Adapter exposing the existing Supervisor as an optional domain workflow."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from langgraph.checkpoint.base import BaseCheckpointSaver

from luxar.application.agent_results import (
    agent_state_to_result,
    agent_user_message_for_state,
)
from luxar.application.agent_runner import (
    AgentWorkflowProgress,
    AgentWorkflowRunResult,
    resume_agent_workflow,
    run_agent_workflow,
)
from luxar.application.agent_state import AgentRuntimeContext
from luxar.database.persistence import PersistencePort
from luxar.domain.continuous_agent.requests import ToolApprovalRequest
from luxar.domain.continuous_agent.steps import DomainWorkflowCall
from luxar.ports.domain_workflow import (
    DomainWorkflowDescriptor,
    DomainWorkflowExecutionContext,
    DomainWorkflowOutcome,
)


AgentRunner = Callable[..., AgentWorkflowRunResult]


class ProjectChangeWorkflow:
    """Run the mature task graph only when the top Agent explicitly selects it."""

    descriptor = DomainWorkflowDescriptor(
        name="project.change",
        description=(
            "用于复杂、多文件或需要非回归验收的工程改造；内部复用项目检查、"
            "任务图、事务式代码变更、构建/设备验证和审批恢复"
        ),
    )

    def __init__(
        self,
        *,
        runtime_context: AgentRuntimeContext,
        checkpointer: BaseCheckpointSaver,
        persistence: PersistencePort | None = None,
        max_steps: int = 80,
        runner: AgentRunner = run_agent_workflow,
        resumer: AgentRunner = resume_agent_workflow,
    ) -> None:
        self._runtime_context = runtime_context
        self._checkpointer = checkpointer
        self._persistence = persistence
        self._max_steps = max_steps
        self._runner = runner
        self._resumer = resumer

    @staticmethod
    def _thread_id(call: DomainWorkflowCall, session_id: str) -> str:
        digest = hashlib.sha256(
            f"{session_id}:{call.call_id}".encode("utf-8")
        ).hexdigest()[:32]
        return f"project_change_{digest}"

    @staticmethod
    def _progress_reporter(
        context: DomainWorkflowExecutionContext,
    ) -> Callable[[AgentWorkflowProgress], None] | None:
        if context.event_reporter is None:
            return None

        def report(progress: AgentWorkflowProgress) -> None:
            payload: dict[str, object] = {
                "stage": progress.node,
                "message": progress.message,
                "attempts": progress.step_count,
                "phase": progress.phase,
                "tools": list(progress.tools),
                "task_id": progress.task_id,
            }
            if progress.detail:
                payload["detail"] = progress.detail
            context.event_reporter("progress", payload)

        return report

    @staticmethod
    def _outcome(
        call: DomainWorkflowCall,
        result: AgentWorkflowRunResult,
    ) -> DomainWorkflowOutcome:
        state = result.state
        result_payload = agent_state_to_result(state)
        if result.pending_approval is not None:
            approval = result.pending_approval
            planned = "；".join(approval.planned_actions[:8])
            summary = approval.summary
            if planned:
                summary += "\n批准后将执行：" + planned
            return DomainWorkflowOutcome(
                status="waiting_approval",
                summary=summary,
                result=result_payload,
                pending_approval=ToolApprovalRequest(
                    request_id=(
                        f"domain:{call.call_id}:{approval.task_id}"
                    ),
                    call_id=call.call_id,
                    tool_name="project.change",
                    summary=summary,
                    risk=(
                        "device"
                        if approval.operation.startswith("device.")
                        else "write"
                    ),
                ),
            )
        raw_status = state.get("status", "failed")
        status = {
            "completed": "completed",
            "awaiting_user": "waiting_input",
            "blocked": "blocked",
            "failed": "failed",
        }.get(raw_status, "failed")
        return DomainWorkflowOutcome(
            status=status,  # type: ignore[arg-type]
            summary=agent_user_message_for_state(state),
            result=result_payload,
        )

    def start(
        self,
        call: DomainWorkflowCall,
        context: DomainWorkflowExecutionContext,
    ) -> DomainWorkflowOutcome:
        result = self._runner(
            initial_state={
                "task_text": call.task,
                "status": "running",
                "trace": [],
                "step_count": 0,
                "max_steps": self._max_steps,
            },
            context=self._runtime_context,
            thread_id=self._thread_id(call, context.session_id),
            checkpointer=self._checkpointer,
            persistence=self._persistence,
            project_key=context.project_key,
            progress_reporter=self._progress_reporter(context),
        )
        return self._outcome(call, result)

    def resume(
        self,
        call: DomainWorkflowCall,
        context: DomainWorkflowExecutionContext,
        *,
        approved: bool,
        feedback: str = "",
    ) -> DomainWorkflowOutcome:
        result = self._resumer(
            thread_id=self._thread_id(call, context.session_id),
            context=self._runtime_context,
            checkpointer=self._checkpointer,
            approved=approved,
            feedback=feedback,
            persistence=self._persistence,
            project_key=context.project_key,
            progress_reporter=self._progress_reporter(context),
        )
        return self._outcome(call, result)


__all__ = ["ProjectChangeWorkflow"]
