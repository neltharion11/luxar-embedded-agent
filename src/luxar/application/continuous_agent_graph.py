"""Minimal conversation-first model -> tool -> model LangGraph loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from luxar.application.continuous_agent_state import ContinuousAgentState
from luxar.application.domain_workflow_registry import DomainWorkflowRegistry
from luxar.application.continuous_agent_steering import SteeringMessage
from luxar.application.tool_registry import ToolRegistry
from luxar.domain.continuous_agent.events import ConversationEvent
from luxar.domain.continuous_agent.failures import ContinuousAgentFailure
from luxar.domain.continuous_agent.requests import MissingInputRequest
from luxar.domain.continuous_agent.steps import (
    AgentStepContext,
    AskUser,
    AssistantReply,
    DomainWorkflowCall,
    FinishObjective,
    ToolCallBatch,
)
from luxar.domain.agent.objectives import ProjectObjective
from luxar.ports.agent_step import AgentStepPort
from luxar.ports.agent_reply import AgentReplyStreamerPort
from luxar.ports.agent_tool import AgentToolExecutionContext
from luxar.ports.domain_workflow import DomainWorkflowExecutionContext
from luxar.ports.context_compactor import AgentContextCompactorPort
from luxar.ports.errors import CapabilityError


@dataclass(frozen=True)
class ContinuousAgentRuntimeContext:
    stepper: AgentStepPort
    tools: ToolRegistry
    reply_streamer: AgentReplyStreamerPort | None = None
    project_path: Path | None = None
    domain_workflows: DomainWorkflowRegistry = field(
        default_factory=DomainWorkflowRegistry
    )
    context_compactor: AgentContextCompactorPort | None = None
    drain_steering: Callable[[], list[SteeringMessage]] | None = None
    cancellation_requested: Callable[[], bool] | None = None
    event_reporter: Callable[[str, dict[str, object]], None] | None = None


def _report_runtime_event(
    runtime: Runtime[ContinuousAgentRuntimeContext],
    event: str,
    data: dict[str, object],
) -> None:
    reporter = runtime.context.event_reporter
    if reporter is not None:
        reporter(event, data)


def compact_context(
    state: ContinuousAgentState,
    runtime: Runtime[ContinuousAgentRuntimeContext],
) -> dict[str, object]:
    compactor = runtime.context.context_compactor
    if compactor is None:
        return {}
    cursor = state.get("compaction_cursor", 0)
    candidates = [
        event
        for event in state.get("events", [])
        if cursor <= 0 or event.sequence > cursor
    ]
    if len(candidates) <= 80:
        return {}
    to_compact = candidates[:-40]
    if not to_compact:
        return {}
    try:
        summary = compactor.compact_context(
            previous_summary=state.get("context_summary", ""),
            events=to_compact,
        ).strip()
    except Exception:
        return {}
    if not summary:
        return {}
    return {
        "context_summary": summary,
        "compaction_cursor": max(event.sequence for event in to_compact),
    }


def ingest_steering(
    state: ContinuousAgentState,
    runtime: Runtime[ContinuousAgentRuntimeContext],
) -> dict[str, object]:
    cancelled = (
        runtime.context.cancellation_requested is not None
        and runtime.context.cancellation_requested()
    )
    messages = (
        runtime.context.drain_steering()
        if runtime.context.drain_steering is not None
        else []
    )
    events = [
        _event(
            state,
            suffix=f"steering:{item.steering_id}",
            kind="user_message",
            payload={"content": item.message, "steering": True},
            offset=index,
        )
        for index, item in enumerate(messages)
    ]
    return {
        "cancel_requested": cancelled or state.get("cancel_requested", False),
        **({"events": events} if events else {}),
    }


def route_after_steering(
    state: ContinuousAgentState,
) -> Literal["decide", "cancel"]:
    return "cancel" if state.get("cancel_requested", False) else "decide"


def cancel_turn(state: ContinuousAgentState) -> dict[str, object]:
    objective = state.get("active_objective")
    if isinstance(objective, dict):
        objective = ProjectObjective.model_validate(objective)
    update: dict[str, object] = {
        "turn_status": "cancelled",
        "objective_status": (
            "abandoned" if objective is not None else state.get("objective_status", "none")
        ),
        "pending_request": None,
        "events": [
            _event(
                state,
                suffix="cancelled",
                kind="assistant_message",
                payload={"content": "已在安全边界停止当前任务，会话仍可继续。"},
            )
        ],
    }
    if objective is not None:
        update["active_objective"] = objective.model_copy(
            update={"status": "cancelled", "revision": objective.revision + 1}
        )
    return update


def _next_sequence(state: ContinuousAgentState) -> int:
    return max((item.sequence for item in state.get("events", [])), default=0) + 1


def _event(
    state: ContinuousAgentState,
    *,
    suffix: str,
    kind: str,
    payload: dict[str, object],
    offset: int = 0,
) -> ConversationEvent:
    event_id = f"{state.get('turn_id', 'turn')}:{suffix}"
    existing = next(
        (
            item
            for item in state.get("events", [])
            if item.event_id == event_id
        ),
        None,
    )
    if existing is not None:
        return existing
    return ConversationEvent(
        event_id=event_id,
        turn_id=state.get("turn_id", "turn"),
        kind=kind,  # type: ignore[arg-type]
        sequence=_next_sequence(state) + offset,
        payload=payload,
    )


def decide_next_step(
    state: ContinuousAgentState,
    runtime: Runtime[ContinuousAgentRuntimeContext],
) -> dict[str, object]:
    step_count = state.get("step_count", 0) + 1
    if step_count > state.get("max_steps", 40):
        return {
            "step_count": step_count,
            "turn_status": "failed",
            "last_failure": ContinuousAgentFailure(
                category="internal",
                code="step_budget_exhausted",
                message="持续 Agent 达到最大步骤预算",
                retryable=False,
            ),
        }

    _report_runtime_event(
        runtime,
        "phase_changed",
        {"phase": "agent_decision", "message": "正在理解需求并决定下一步"},
    )
    tool_results = [
        {
            "call_id": call.call_id,
            "tool_name": call.tool_name,
            "status": call.status,
            "result": call.result,
            "evidence_ids": call.evidence_ids,
            "failure": (
                call.failure.model_dump(mode="json")
                if call.failure is not None
                else None
            ),
        }
        for call in state.get("tool_calls", {}).values()
        if call.status in {"succeeded", "failed", "rejected", "indeterminate"}
    ][-20:]
    pending = state.get("pending_request")
    compaction_cursor = state.get("compaction_cursor", 0)
    visible_events = [
        event
        for event in state.get("events", [])
        if compaction_cursor <= 0 or event.sequence > compaction_cursor
    ][-100:]
    context = AgentStepContext(
        session_id=state["session_id"],
        turn_id=state["turn_id"],
        project_key=state["project_key"],
        context_summary=state.get("context_summary", ""),
        recent_events=visible_events,
        active_objective=state.get("active_objective"),
        pending_request=(pending if isinstance(pending, MissingInputRequest) else None),
        resolved_inputs=state.get("resolved_inputs", {}),
        tools=runtime.context.tools.descriptors(),
        domain_workflows=[
            item.model_dump(mode="json")
            for item in runtime.context.domain_workflows.descriptors()
        ],
        latest_tool_results=tool_results,
        latest_domain_results=list(state.get("domain_calls", {}).values())[-10:],
    )
    try:
        step = runtime.context.stepper.decide_next_step(context)
    except CapabilityError as error:
        return {
            "step_count": step_count,
            "turn_status": "failed",
            "last_failure": ContinuousAgentFailure(
                category="model",
                code=error.category,
                message="模型无法生成有效的下一步决策",
                retryable=error.retryable,
            ),
        }
    except Exception:
        return {
            "step_count": step_count,
            "turn_status": "failed",
            "last_failure": ContinuousAgentFailure(
                category="model",
                code="decision_failed",
                message="模型下一步决策失败",
                retryable=True,
            ),
        }
    return {
        "step_count": step_count,
        "next_step": step,
        "turn_status": "running",
        "last_failure": None,
    }


def route_after_decision(
    state: ContinuousAgentState,
) -> Literal["reply", "ask", "tools", "domain", "finish", "fail"]:
    if state.get("turn_status") == "failed":
        return "fail"
    step = state.get("next_step")
    if isinstance(step, AssistantReply):
        return "reply"
    if isinstance(step, AskUser):
        return "ask"
    if isinstance(step, ToolCallBatch):
        return "tools"
    if isinstance(step, DomainWorkflowCall):
        return "domain"
    if isinstance(step, FinishObjective):
        return "finish"
    return "fail"


def render_streaming_reply(
    state: ContinuousAgentState,
    runtime: Runtime[ContinuousAgentRuntimeContext],
) -> dict[str, object]:
    step = state["next_step"]
    assert isinstance(step, AssistantReply)
    content_parts: list[str] = []
    response_status = "complete"
    streamer = runtime.context.reply_streamer
    if streamer is None:
        chunks = [step.content]
    else:
        tool_results = [
            {
                "call_id": call.call_id,
                "tool_name": call.tool_name,
                "status": call.status,
                "result": call.result,
                "evidence_ids": call.evidence_ids,
                "failure": (
                    call.failure.model_dump(mode="json")
                    if call.failure is not None
                    else None
                ),
            }
            for call in state.get("tool_calls", {}).values()
            if call.status in {"succeeded", "failed", "rejected", "indeterminate"}
        ][-20:]
        pending = state.get("pending_request")
        compaction_cursor = state.get("compaction_cursor", 0)
        visible_events = [
            event
            for event in state.get("events", [])
            if compaction_cursor <= 0 or event.sequence > compaction_cursor
        ][-100:]
        reply_context = AgentStepContext(
            session_id=state["session_id"],
            turn_id=state["turn_id"],
            project_key=state["project_key"],
            context_summary=state.get("context_summary", ""),
            recent_events=visible_events,
            active_objective=state.get("active_objective"),
            pending_request=(
                pending if isinstance(pending, MissingInputRequest) else None
            ),
            resolved_inputs=state.get("resolved_inputs", {}),
            tools=runtime.context.tools.descriptors(),
            domain_workflows=[
                item.model_dump(mode="json")
                for item in runtime.context.domain_workflows.descriptors()
            ],
            latest_tool_results=tool_results,
            latest_domain_results=list(state.get("domain_calls", {}).values())[-10:],
        )
        chunks = streamer.stream_reply(draft=step.content, context=reply_context)
    try:
        for chunk in chunks:
            text = str(chunk)
            if not text:
                continue
            content_parts.append(text)
            _report_runtime_event(runtime, "token", {"token": text})
    except Exception:
        response_status = "degraded"
        if not content_parts:
            content_parts.append(step.content)
            _report_runtime_event(runtime, "token", {"token": step.content})
        notice = "\n\n任务结果已经保留，但自然语言说明生成中断；详细证据可在工作台查看。"
        content_parts.append(notice)
        _report_runtime_event(runtime, "token", {"token": notice})
    content = "".join(content_parts).strip() or step.content
    return {
        "turn_status": "completed",
        "response_status": response_status,
        "pending_request": None,
        "events": [
            _event(
                state,
                suffix="assistant",
                kind="assistant_message",
                payload={"content": content},
            )
        ],
    }


def request_missing_input(state: ContinuousAgentState) -> dict[str, object]:
    step = state["next_step"]
    assert isinstance(step, AskUser)
    return {
        "turn_status": "waiting_input",
        "pending_request": step.request,
        "events": [
            _event(
                state,
                suffix="missing-input",
                kind="pending_request",
                payload=step.request.model_dump(mode="json"),
            ),
            _event(
                state,
                suffix="assistant",
                kind="assistant_message",
                payload={"content": step.request.prompt},
                offset=1,
            ),
        ],
    }


def execute_tools(
    state: ContinuousAgentState,
    runtime: Runtime[ContinuousAgentRuntimeContext],
) -> dict[str, object]:
    if (
        runtime.context.cancellation_requested is not None
        and runtime.context.cancellation_requested()
    ):
        return {"cancel_requested": True, "turn_status": "running"}
    step = state["next_step"]
    assert isinstance(step, ToolCallBatch)
    tool_context = AgentToolExecutionContext(
        session_id=state["session_id"],
        turn_id=state["turn_id"],
        project_key=state["project_key"],
        project_path=runtime.context.project_path,
    )
    approvals = state.get("tool_approvals", {})
    calls = dict(state.get("tool_calls", {}))
    events: list[ConversationEvent] = []
    evidence_ids = list(state.get("evidence_ids", []))
    pending = None
    base_offset = 0
    for call in step.calls:
        _report_runtime_event(
            runtime,
            "tool_call",
            {
                "conversation_event_id": (
                    f"{state.get('turn_id', 'turn')}:tool-call:{call.call_id}"
                ),
                "call_id": call.call_id,
                "tool_name": call.tool_name,
                "tool_call": call.tool_name,
                "arguments": call.arguments,
            },
        )
        outcome = runtime.context.tools.dispatch(
            call,
            tool_context,
            approved=approvals.get(call.call_id),
        )
        calls[call.call_id] = outcome.call
        if outcome.pending_approval is None:
            _report_runtime_event(
                runtime,
                "tool_result",
                {
                    "conversation_event_id": (
                        f"{state.get('turn_id', 'turn')}:tool-result:{call.call_id}"
                    ),
                    **outcome.call.model_dump(mode="json"),
                },
            )
        evidence_ids.extend(outcome.call.evidence_ids)
        events.append(
            _event(
                state,
                suffix=f"tool-call:{call.call_id}",
                kind="tool_call",
                payload={
                    "call_id": call.call_id,
                    "tool_name": call.tool_name,
                    "arguments": call.arguments,
                },
                offset=base_offset,
            )
        )
        base_offset += 1
        if outcome.pending_approval is not None:
            pending = outcome.pending_approval
            events.append(
                _event(
                    state,
                    suffix=f"approval:{call.call_id}",
                    kind="pending_request",
                    payload=pending.model_dump(mode="json"),
                    offset=base_offset,
                )
            )
            base_offset += 1
            break
        events.append(
            _event(
                state,
                suffix=f"tool-result:{call.call_id}",
                kind="tool_result",
                payload=outcome.call.model_dump(mode="json"),
                offset=base_offset,
            )
        )
        base_offset += 1
    return {
        "tool_calls": calls,
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "events": events,
        "pending_request": pending,
        "turn_status": "waiting_approval" if pending is not None else "running",
    }


def route_after_tools(
    state: ContinuousAgentState,
) -> Literal["decide", "approval", "cancel"]:
    if state.get("cancel_requested", False):
        return "cancel"
    return (
        "approval"
        if state.get("turn_status") == "waiting_approval"
        else "decide"
    )


def execute_domain_workflow(
    state: ContinuousAgentState,
    runtime: Runtime[ContinuousAgentRuntimeContext],
) -> dict[str, object]:
    if (
        runtime.context.cancellation_requested is not None
        and runtime.context.cancellation_requested()
    ):
        return {"cancel_requested": True, "turn_status": "running"}
    step = state["next_step"]
    assert isinstance(step, DomainWorkflowCall)
    execution_context = DomainWorkflowExecutionContext(
        session_id=state["session_id"],
        turn_id=state["turn_id"],
        project_key=state["project_key"],
        project_path=runtime.context.project_path,
        event_reporter=runtime.context.event_reporter,
    )
    _report_runtime_event(
        runtime,
        "tool_call",
        {
            "conversation_event_id": (
                f"{state.get('turn_id', 'turn')}:domain-call:{step.call_id}"
            ),
            "call_id": step.call_id,
            "tool_name": step.workflow_name,
            "tool_call": step.workflow_name,
            "arguments": {"task": step.task},
        },
    )
    approvals = state.get("domain_approvals", {})
    feedback = state.get("domain_approval_feedback", {}).get(step.call_id, "")
    outcome = runtime.context.domain_workflows.dispatch(
        step,
        execution_context,
        approved=approvals.get(step.call_id),
        feedback=feedback,
    )
    calls = dict(state.get("domain_calls", {}))
    calls[step.call_id] = {
        "call_id": step.call_id,
        "workflow_name": step.workflow_name,
        "task": step.task,
        "status": outcome.status,
        "summary": outcome.summary,
        "result": outcome.result,
    }
    if outcome.pending_approval is None:
        _report_runtime_event(
            runtime,
            "tool_result",
            {
                "conversation_event_id": (
                    f"{state.get('turn_id', 'turn')}:domain-result:{step.call_id}"
                ),
                **calls[step.call_id],
            },
        )
    request = outcome.pending_approval
    objective = state.get("active_objective")
    raw_objective = outcome.result.get("objective")
    if raw_objective is not None:
        try:
            objective = ProjectObjective.model_validate(raw_objective)
        except (TypeError, ValueError):
            pass
    if objective is None:
        objective = ProjectObjective(
            objective_id=f"domain:{step.call_id}",
            title=step.task[:240],
            description=step.task,
            source_message_ids=[state["turn_id"]],
        )
    objective_status = {
        "completed": "completed",
        "blocked": "blocked",
        "failed": "blocked",
    }.get(outcome.status, "active")
    objective = objective.model_copy(
        update={
            "status": {
                "completed": "completed",
                "blocked": "blocked",
            }.get(objective_status, "active")
        }
    )
    return {
        "domain_calls": calls,
        "active_objective": objective,
        "objective_status": objective_status,
        "pending_request": request,
        "turn_status": (
            "waiting_approval" if request is not None else "running"
        ),
        "events": [
            _event(
                state,
                suffix=(
                    f"domain-approval:{request.request_id}"
                    if request is not None
                    else f"domain-result:{step.call_id}"
                ),
                kind="pending_request" if request is not None else "tool_result",
                payload=(
                    request.model_dump(mode="json")
                    if request is not None
                    else calls[step.call_id]
                ),
            )
        ],
    }


def route_after_domain(
    state: ContinuousAgentState,
) -> Literal["decide", "approval", "cancel"]:
    if state.get("cancel_requested", False):
        return "cancel"
    return (
        "approval"
        if state.get("turn_status") == "waiting_approval"
        else "decide"
    )


def await_tool_approval(state: ContinuousAgentState) -> dict[str, object]:
    pending = state.get("pending_request")
    if pending is None or getattr(pending, "kind", None) != "approval":
        return {
            "turn_status": "failed",
            "last_failure": ContinuousAgentFailure(
                category="internal",
                code="missing_approval_request",
                message="工具审批状态不完整",
                retryable=False,
            ),
        }
    decision = interrupt(pending.model_dump(mode="json"))
    approved = bool(decision.get("approved", False))
    approvals = dict(state.get("tool_approvals", {}))
    approvals[pending.call_id] = approved
    return {
        "tool_approvals": approvals,
        "pending_request": None,
        "turn_status": "running",
        "events": [
            _event(
                state,
                suffix=f"approval-decision:{pending.call_id}",
                kind="approval_decision",
                payload={
                    "call_id": pending.call_id,
                    "approved": approved,
                    "feedback": str(decision.get("feedback", "")),
                },
            )
        ],
    }


def await_domain_approval(state: ContinuousAgentState) -> dict[str, object]:
    pending = state.get("pending_request")
    if pending is None or getattr(pending, "kind", None) != "approval":
        return {
            "turn_status": "failed",
            "last_failure": ContinuousAgentFailure(
                category="internal",
                code="missing_domain_approval_request",
                message="领域工作流审批状态不完整",
                retryable=False,
            ),
        }
    decision = interrupt(pending.model_dump(mode="json"))
    approvals = dict(state.get("domain_approvals", {}))
    feedback = dict(state.get("domain_approval_feedback", {}))
    approvals[pending.call_id] = bool(decision.get("approved", False))
    feedback[pending.call_id] = str(decision.get("feedback", ""))
    return {
        "domain_approvals": approvals,
        "domain_approval_feedback": feedback,
        "pending_request": None,
        "turn_status": "running",
        "events": [
            _event(
                state,
                suffix=f"domain-approval-decision:{pending.request_id}",
                kind="approval_decision",
                payload={
                    "call_id": pending.call_id,
                    "approved": approvals[pending.call_id],
                    "feedback": feedback[pending.call_id],
                },
            )
        ],
    }


def finish_objective(
    state: ContinuousAgentState,
    runtime: Runtime[ContinuousAgentRuntimeContext],
) -> dict[str, object]:
    step = state["next_step"]
    assert isinstance(step, FinishObjective)
    objective = state.get("active_objective")
    if isinstance(objective, dict):
        objective = ProjectObjective.model_validate(objective)
    updated_objective = (
        objective.model_copy(
            update={
                "status": (
                    "completed" if step.outcome == "completed" else "cancelled"
                ),
                "revision": objective.revision + 1,
            }
        )
        if objective is not None
        else None
    )
    streamed = render_streaming_reply(
        {**state, "next_step": AssistantReply(content=step.summary)},
        runtime,
    )
    assistant_event = streamed["events"][0]
    assistant_content = assistant_event.payload["content"]
    update: dict[str, object] = {
        "objective_status": step.outcome,
        "turn_status": "completed",
        "response_status": streamed.get("response_status", "complete"),
        "pending_request": None,
        "events": [
            _event(
                state,
                suffix="objective-finished",
                kind="objective_updated",
                payload={"outcome": step.outcome, "summary": step.summary},
            ),
            _event(
                state,
                suffix="assistant",
                kind="assistant_message",
                payload={"content": assistant_content},
                offset=1,
            ),
        ],
    }
    if updated_objective is not None:
        update["active_objective"] = updated_objective
    return update


def fail_turn(state: ContinuousAgentState) -> dict[str, object]:
    failure = state.get("last_failure") or ContinuousAgentFailure(
        category="internal",
        code="missing_agent_step",
        message="持续 Agent 没有生成可执行决策",
        retryable=False,
    )
    return {
        "turn_status": "failed",
        "last_failure": failure,
        "events": [
            _event(
                state,
                suffix="failure",
                kind="failure",
                payload=failure.model_dump(mode="json"),
            )
        ],
    }


def build_continuous_agent_graph(
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    builder = StateGraph(
        ContinuousAgentState,
        context_schema=ContinuousAgentRuntimeContext,
    )
    builder.add_node("compact_context", compact_context)
    builder.add_node("ingest_steering", ingest_steering)
    builder.add_node("cancel_turn", cancel_turn)
    builder.add_node("decide_next_step", decide_next_step)
    builder.add_node("render_reply", render_streaming_reply)
    builder.add_node("request_missing_input", request_missing_input)
    builder.add_node("execute_tools", execute_tools)
    builder.add_node("execute_domain_workflow", execute_domain_workflow)
    builder.add_node("await_tool_approval", await_tool_approval)
    builder.add_node("await_domain_approval", await_domain_approval)
    builder.add_node("finish_objective", finish_objective)
    builder.add_node("fail_turn", fail_turn)
    builder.add_edge(START, "compact_context")
    builder.add_edge("compact_context", "ingest_steering")
    builder.add_conditional_edges(
        "ingest_steering",
        route_after_steering,
        {"decide": "decide_next_step", "cancel": "cancel_turn"},
    )
    builder.add_conditional_edges(
        "decide_next_step",
        route_after_decision,
        {
            "reply": "render_reply",
            "ask": "request_missing_input",
            "tools": "execute_tools",
            "domain": "execute_domain_workflow",
            "finish": "finish_objective",
            "fail": "fail_turn",
        },
    )
    builder.add_conditional_edges(
        "execute_tools",
        route_after_tools,
        {
            "decide": "ingest_steering",
            "approval": "await_tool_approval",
            "cancel": "cancel_turn",
        },
    )
    builder.add_conditional_edges(
        "execute_domain_workflow",
        route_after_domain,
        {
            "decide": "ingest_steering",
            "approval": "await_domain_approval",
            "cancel": "cancel_turn",
        },
    )
    builder.add_edge("await_tool_approval", "execute_tools")
    builder.add_edge("await_domain_approval", "execute_domain_workflow")
    builder.add_edge("render_reply", END)
    builder.add_edge("request_missing_input", END)
    builder.add_edge("finish_objective", END)
    builder.add_edge("fail_turn", END)
    builder.add_edge("cancel_turn", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["ContinuousAgentRuntimeContext", "build_continuous_agent_graph"]
