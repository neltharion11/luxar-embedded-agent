"""HTTP/SSE projection for the conversation-first continuous Agent runtime."""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import replace
from collections.abc import Generator
from collections.abc import Callable

from fastapi.responses import StreamingResponse
from langgraph.checkpoint.base import BaseCheckpointSaver

from luxar.application.continuous_agent_graph import ContinuousAgentRuntimeContext
from luxar.application.continuous_agent_identity import begin_continuous_agent_turn
from luxar.application.continuous_agent_identity import ContinuousAgentTurnIdentity
from luxar.application.continuous_agent_runner import (
    ContinuousAgentRunResult,
    resume_continuous_agent_workflow,
    run_continuous_agent_workflow,
)
from luxar.database.persistence import PendingApprovalRecord, PersistencePort
from luxar.domain.continuous_agent.events import ConversationEvent


_WORKER_LOCK = threading.Lock()
_ACTIVE_WORKERS: set[threading.Thread] = set()


def _start_worker(target: Callable[[], None], *, name: str) -> None:
    def tracked_target() -> None:
        try:
            target()
        finally:
            with _WORKER_LOCK:
                _ACTIVE_WORKERS.discard(threading.current_thread())

    worker = threading.Thread(target=tracked_target, name=name, daemon=True)
    with _WORKER_LOCK:
        _ACTIVE_WORKERS.add(worker)
    worker.start()


def wait_for_continuous_agent_workers(timeout: float = 30.0) -> None:
    """Give in-flight workers time to release durable storage during shutdown."""
    with _WORKER_LOCK:
        workers = list(_ACTIVE_WORKERS)
    deadline = time.monotonic() + max(0.0, timeout)
    for worker in workers:
        worker.join(timeout=max(0.0, deadline - time.monotonic()))


def _sse(event: str, data: dict[str, object] | str) -> str:
    payload = (
        data
        if isinstance(data, str)
        else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    )
    return (
        f"event: {event}\n"
        f"data: {payload}\n\n"
    )


def _assistant_message(result: ContinuousAgentRunResult) -> str:
    for event in reversed(result.state.get("events", [])):
        if event.kind == "assistant_message":
            content = str(event.payload.get("content", "")).strip()
            if content:
                return content
    if result.pending_approval is not None:
        return result.pending_approval.summary
    failure = result.state.get("last_failure")
    if failure is not None:
        return failure.message
    return "本轮没有产生可展示的回复。"


def _result_envelope(result: ContinuousAgentRunResult) -> dict[str, object]:
    pending = result.state.get("pending_request")
    failure = result.state.get("last_failure")
    return {
        "status": result.state.get("turn_status", "failed"),
        "session_id": result.state.get("session_id", result.thread_id),
        "turn_id": result.state.get("turn_id"),
        "objective_status": result.state.get("objective_status", "none"),
        "pending_request": (
            pending.model_dump(mode="json") if pending is not None else None
        ),
        "tool_calls": {
            call_id: call.model_dump(mode="json")
            for call_id, call in result.state.get("tool_calls", {}).items()
        },
        "domain_calls": result.state.get("domain_calls", {}),
        "evidence_ids": result.state.get("evidence_ids", []),
        "failure": (
            failure.model_dump(mode="json") if failure is not None else None
        ),
    }


def _headers(session_id: str, turn_id: str) -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "X-LUXAR-Thread-ID": turn_id,
        "X-LUXAR-Session-ID": session_id,
        "X-LUXAR-Turn-ID": turn_id,
    }


def _persist_session_projection(
    persistence: PersistencePort,
    result: ContinuousAgentRunResult,
) -> None:
    objective = result.state.get("active_objective")
    objective_status = result.state.get("objective_status", "none")
    raw_objective_id = (
        getattr(objective, "objective_id", None)
        if objective is not None
        else None
    )
    if raw_objective_id is None and isinstance(objective, dict):
        raw_objective_id = objective.get("objective_id")
    active_objective_id = (
        str(raw_objective_id)
        if raw_objective_id is not None
        and objective_status in {"proposed", "active", "blocked"}
        else None
    )
    persistence.update_agent_session_state(
        result.state.get("session_id", result.thread_id),
        active_objective_id=active_objective_id,
        context_summary=result.state.get("context_summary", ""),
        compaction_cursor=result.state.get("compaction_cursor", 0),
    )


def _project_graph_events(
    events: list[ConversationEvent],
    *,
    turn_id: str,
) -> list[tuple[str, dict[str, object]]]:
    projected: list[tuple[str, dict[str, object]]] = []
    for event in events:
        if event.turn_id != turn_id:
            continue
        payload = {"conversation_event_id": event.event_id, **event.payload}
        if event.kind == "tool_call":
            payload.setdefault("tool_call", payload.get("tool_name", ""))
            projected.append(("tool_call", payload))
        elif event.kind == "tool_result":
            projected.append(("tool_result", payload))
        elif event.kind == "approval_decision":
            projected.append(("approval_decision", payload))
        elif event.kind == "failure":
            projected.append(("error", payload))
    return projected


def _persist_new_graph_events(
    persistence: PersistencePort,
    *,
    stream_id: str,
    projected: list[tuple[str, dict[str, object]]],
) -> list[tuple[str, dict[str, object]]]:
    existing_ids = {
        str(item.data.get("conversation_event_id"))
        for item in persistence.list_conversation_stream_events(
            stream_id,
            after_sequence=0,
            limit=2_000,
        )
        if isinstance(item.data, dict)
        and item.data.get("conversation_event_id") is not None
    }
    new_events = [
        item
        for item in projected
        if str(item[1].get("conversation_event_id")) not in existing_ids
    ]
    for event_name, data in new_events:
        persistence.append_conversation_stream_event(
            stream_id,
            event=event_name,
            data=data,
        )
    return new_events


def run_continuous_agent_http_turn(
    *,
    project_name: str,
    root_index: int,
    project_key: str,
    message: str,
    requested_session_id: str | None,
    client_turn_id: str | None,
    resolved_inputs: dict[str, object],
    max_steps: int,
    context: ContinuousAgentRuntimeContext | None,
    persistence: PersistencePort,
    checkpointer: BaseCheckpointSaver,
    runtime_metadata: dict[str, object] | None = None,
    identity: ContinuousAgentTurnIdentity | None = None,
    on_complete: Callable[[], None] | None = None,
) -> StreamingResponse:
    identity = identity or begin_continuous_agent_turn(
        persistence,
        project_key=project_key,
        user_message=message,
        requested_session_id=requested_session_id,
        client_turn_id=client_turn_id,
    )
    session_id = identity.session.session_id
    turn_id = identity.turn.turn_id
    if identity.replayed:
        replay_text = identity.turn.assistant_message

        def replay() -> Generator[str, None, None]:
            yield _sse(
                "turn_status",
                {"status": identity.turn.status, "replayed": True},
            )
            if replay_text:
                yield _sse("token", {"token": replay_text})
            yield _sse("done", "[DONE]")

        return StreamingResponse(
            replay(),
            media_type="text/event-stream",
            headers=_headers(session_id, turn_id),
        )

    if context is None:
        raise ValueError("持续 Agent runtime context 尚未初始化")

    persistence.start_conversation_stream(
        thread_id=turn_id,
        task_key=project_key,
        user_message=message,
    )
    runtime_config: dict[str, object] = {
        "workflow_family": "continuous_agent",
        "continuous_agent_v2": True,
        "session_id": session_id,
        "turn_id": turn_id,
        "project_name": project_name,
        "root_index": root_index,
    }
    if runtime_metadata:
        runtime_config.update(runtime_metadata)
    persistence.start_run(
        thread_id=turn_id,
        task_key=project_key,
        project_name=project_name,
        root_index=root_index,
        task_text=message,
        runtime_config=runtime_config,
    )
    initial_state: dict[str, object] = {
        "session_id": session_id,
        "turn_id": turn_id,
        "project_key": project_key,
        "session_status": "active",
        "turn_status": "running",
        "events": [
            ConversationEvent(
                event_id=f"{turn_id}:user",
                turn_id=turn_id,
                kind="user_message",
                sequence=0,
                payload={"content": message},
            )
        ],
        "step_count": 0,
        "max_steps": max_steps,
        # These fields describe one Turn, while the checkpoint thread is kept
        # at Session scope. Explicitly reset tool/domain evidence so prior
        # calls cannot leak into a new result or bias the next model decision.
        # pending_request intentionally survives for natural follow-up input.
        "resolved_inputs": {},
        "tool_calls": {},
        "tool_approvals": {},
        "domain_calls": {},
        "domain_approvals": {},
        "domain_approval_feedback": {},
        "evidence_ids": [],
        "response_status": "complete",
        # A cancellation belongs to one Turn.  LangGraph checkpoints are kept at
        # Session scope, so every fresh Turn must explicitly clear the old flag.
        "cancel_requested": False,
    }
    if resolved_inputs:
        initial_state["resolved_inputs"] = resolved_inputs
    outbound: queue.Queue[tuple[str, dict[str, object] | str]] = queue.Queue(
        maxsize=512
    )
    finished = threading.Event()
    token_emitted = threading.Event()
    latest_status: dict[str, object] = {
        "stage": "agent_decision",
        "message": "正在理解需求并决定下一步",
        "tools": [],
        "task_id": None,
    }
    published_conversation_event_ids: set[str] = set()

    def enqueue(event: str, data: dict[str, object] | str) -> None:
        try:
            outbound.put_nowait((event, data))
        except queue.Full:
            # Every event is persisted before enqueue. A disconnected or slow
            # browser can replay it without blocking the Agent worker.
            pass

    def publish(event: str, data: dict[str, object] | str) -> None:
        if isinstance(data, dict) and data.get("conversation_event_id"):
            conversation_event_id = str(data["conversation_event_id"])
            if conversation_event_id in published_conversation_event_ids:
                return
            published_conversation_event_ids.add(conversation_event_id)
        if event == "token":
            token_emitted.set()
        if isinstance(data, dict):
            if event == "tool_call":
                tool = data.get("tool_name") or data.get("tool_call")
                latest_status.update(
                    {
                        "stage": str(tool or "tool"),
                        "message": "工具正在执行",
                        "tools": [str(tool)] if tool else [],
                    }
                )
            elif event == "progress":
                latest_status.update(
                    {
                        "stage": data.get("stage", latest_status["stage"]),
                        "message": data.get("message", latest_status["message"]),
                        "tools": data.get("tools", latest_status["tools"]),
                        "task_id": data.get("task_id"),
                    }
                )
            elif event == "phase_changed":
                latest_status.update(
                    {
                        "stage": data.get("phase", latest_status["stage"]),
                        "message": data.get("message", latest_status["message"]),
                    }
                )
        persistence.append_conversation_stream_event(
            turn_id,
            event=event,
            data=data,
        )
        enqueue(event, data)

    runtime_context = replace(context, event_reporter=publish)
    publish(
        "turn_status",
        {"status": "running", "phase": "agent_decision", "message": "正在理解需求"},
    )

    def worker() -> None:
        try:
            result = run_continuous_agent_workflow(
                initial_state=initial_state,  # type: ignore[arg-type]
                context=runtime_context,
                thread_id=session_id,
                checkpointer=checkpointer,
            )
            assistant_message = _assistant_message(result)
            envelope = _result_envelope(result)
            envelope["response_status"] = result.state.get(
                "response_status", "complete"
            )
            _persist_session_projection(persistence, result)
            turn_status = str(envelope["status"])
            stream_status = (
                "pending_approval" if result.pending_approval else "completed"
            )
            if turn_status == "failed":
                stream_status = "failed"

            if result.pending_approval is not None:
                persistence.save_pending_approval(
                    PendingApprovalRecord(
                        task_key=project_key,
                        project_name=project_name,
                        root_index=root_index,
                        thread_id=session_id,
                        request=result.pending_approval.model_dump(mode="json"),
                        runtime_config=runtime_config,
                    )
                )
            projected_events = _persist_new_graph_events(
                persistence,
                stream_id=turn_id,
                projected=_project_graph_events(
                    result.state.get("events", []),
                    turn_id=turn_id,
                ),
            )
            for event_name, data in projected_events:
                enqueue(event_name, data)
            if not token_emitted.is_set():
                publish("token", {"token": assistant_message})
            if result.pending_approval is not None:
                publish(
                    "approval",
                    {
                        "thread_id": session_id,
                        "request": result.pending_approval.model_dump(mode="json"),
                    },
                )
            publish("result", envelope)
            publish("done", "[DONE]")
            persistence.finish_conversation_stream(turn_id, status=stream_status)
            persistence.finish_run(turn_id, status=turn_status, result=envelope)
            failure = envelope.get("failure")
            persistence.finish_agent_turn(
                turn_id,
                status=turn_status,
                assistant_message=assistant_message,
                failure=failure if isinstance(failure, dict) else None,
            )
            persistence.append_exchange(
                project_key,
                thread_id=turn_id,
                user_message=message,
                assistant_message=assistant_message,
            )
        except Exception as error:
            failure = {
                "category": "runtime",
                "code": "continuous_agent_failed",
                "message": "持续 Agent 执行失败",
            }
            publish("error", failure)
            publish("done", "[DONE]")
            persistence.finish_conversation_stream(turn_id, status="failed")
            persistence.finish_run(turn_id, status="failed", result=failure)
            persistence.finish_agent_turn(
                turn_id,
                status="failed",
                assistant_message="持续 Agent 执行失败，请检查服务端日志。",
                failure=failure,
            )
        finally:
            finished.set()
            if on_complete is not None:
                on_complete()

    _start_worker(worker, name=f"luxar-continuous-{turn_id[:8]}")

    def stream() -> Generator[str, None, None]:
        while not finished.is_set() or not outbound.empty():
            try:
                event, data = outbound.get(timeout=1.0)
            except queue.Empty:
                heartbeat = {
                    "stage": latest_status["stage"],
                    "message": "任务仍在执行，正在等待当前步骤返回结果",
                    "attempts": 0,
                    "phase": "heartbeat",
                    "tools": latest_status["tools"],
                    "task_id": latest_status["task_id"],
                }
                yield _sse("progress", heartbeat)
                continue
            yield _sse(event, data)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=_headers(session_id, turn_id),
    )


def resume_continuous_agent_http_approval(
    *,
    record: PendingApprovalRecord,
    approved: bool,
    feedback: str,
    context: ContinuousAgentRuntimeContext,
    persistence: PersistencePort,
    checkpointer: BaseCheckpointSaver,
    on_complete: Callable[[], None] | None = None,
) -> dict[str, object]:
    turn_id = str(record.runtime_config.get("turn_id", ""))
    stream = persistence.get_conversation_stream(turn_id)
    if stream is not None:
        persistence.finish_conversation_stream(turn_id, status="running")
        persistence.append_conversation_stream_event(
            turn_id,
            event="phase_changed",
            data={"phase": "approval_resume", "message": "审批已处理，正在继续任务"},
        )

    first_token = True
    published_conversation_event_ids = {
        str(item.data.get("conversation_event_id"))
        for item in persistence.list_conversation_stream_events(
            turn_id, after_sequence=0, limit=2_000
        )
        if isinstance(item.data, dict)
        and item.data.get("conversation_event_id") is not None
    }

    def publish(event: str, data: dict[str, object] | str) -> None:
        nonlocal first_token
        if stream is None:
            return
        if isinstance(data, dict) and data.get("conversation_event_id"):
            conversation_event_id = str(data["conversation_event_id"])
            if conversation_event_id in published_conversation_event_ids:
                return
            published_conversation_event_ids.add(conversation_event_id)
        if event == "token" and isinstance(data, dict):
            token = str(data.get("token", ""))
            if first_token and token:
                data = {"token": "\n\n" + token}
                first_token = False
        persistence.append_conversation_stream_event(
            turn_id,
            event=event,
            data=data,
        )

    runtime_context = replace(context, event_reporter=publish)

    def worker() -> None:
        try:
            result = resume_continuous_agent_workflow(
                thread_id=record.thread_id,
                context=runtime_context,
                checkpointer=checkpointer,
                approved=approved,
                feedback=feedback,
            )
            assistant_message = _assistant_message(result)
            envelope = _result_envelope(result)
            envelope["response_status"] = result.state.get(
                "response_status", "complete"
            )
            _persist_session_projection(persistence, result)
            _persist_new_graph_events(
                persistence,
                stream_id=turn_id,
                projected=_project_graph_events(
                    result.state.get("events", []),
                    turn_id=turn_id,
                ),
            )
            if result.pending_approval is not None:
                persistence.save_pending_approval(
                    PendingApprovalRecord(
                        task_key=record.task_key,
                        project_name=record.project_name,
                        root_index=record.root_index,
                        thread_id=record.thread_id,
                        request=result.pending_approval.model_dump(mode="json"),
                        runtime_config=record.runtime_config,
                    )
                )
                publish(
                    "approval",
                    {
                        "thread_id": record.thread_id,
                        "request": result.pending_approval.model_dump(mode="json"),
                    },
                )
                persistence.finish_conversation_stream(
                    turn_id, status="pending_approval"
                )
                persistence.finish_agent_turn(
                    turn_id,
                    status="waiting_approval",
                    assistant_message=assistant_message,
                )
                publish("result", envelope)
                publish("done", "[DONE]")
                return

            persistence.complete_approval(record.task_key)
            turn_status = str(envelope["status"])
            persistence.finish_run(turn_id, status=turn_status, result=envelope)
            # Persist the terminal stream payload before marking the durable
            # turn complete.  Callers use the turn status as their completion
            # barrier; publishing it first could expose the earlier
            # waiting-approval result without the resumed tool evidence.
            if first_token:
                persistence.append_conversation_stream_event(
                    turn_id,
                    event="token",
                    data={"token": "\n\n" + assistant_message},
                )
            publish("result", envelope)
            publish("done", "[DONE]")
            if stream is not None:
                persistence.finish_conversation_stream(turn_id, status="completed")
            persistence.finish_agent_turn(
                turn_id,
                status=turn_status,
                assistant_message=assistant_message,
                failure=(
                    envelope["failure"]
                    if isinstance(envelope.get("failure"), dict)
                    else None
                ),
            )
            persistence.append_exchange(
                record.task_key,
                thread_id=turn_id,
                user_message=str(record.runtime_config.get("task_text", "")),
                assistant_message=assistant_message,
            )
        except Exception:
            persistence.append_conversation_stream_event(
                turn_id,
                event="error",
                data={
                    "category": "recovery",
                    "message": "审批后的任务恢复失败",
                },
            )
            persistence.append_conversation_stream_event(
                turn_id, event="done", data="[DONE]"
            )
            persistence.finish_conversation_stream(turn_id, status="failed")
        finally:
            if on_complete is not None:
                on_complete()

    _start_worker(worker, name=f"luxar-continuous-resume-{turn_id[:8]}")
    return {
        "status": "resuming",
        "project": record.project_name,
        "session_id": record.thread_id,
        "turn_id": turn_id,
    }


__all__ = [
    "resume_continuous_agent_http_approval",
    "run_continuous_agent_http_turn",
    "wait_for_continuous_agent_workers",
]
