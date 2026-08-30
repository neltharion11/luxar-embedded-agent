"""HTTP/SSE projection for the conversation-first continuous Agent runtime."""

from __future__ import annotations

import json
import logging
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


_LOGGER = logging.getLogger(__name__)

_WORKER_LOCK = threading.Lock()
_ACTIVE_WORKERS: set[threading.Thread] = set()

# 跨轮历史注入（对应 DSH deriveMessages 的思路）：持续 Agent 每轮都能看到
# 之前轮次的 user/assistant 对话；工具调用与结果仍保持 turn 本地，不跨轮泄漏。
_HISTORY_EVENT_LIMIT = 20
_HISTORY_CONTENT_CHARACTERS = 2_000
_RECOVERABLE_STREAM_CONTENT_CHARACTERS = 8_000


def _recoverable_stream_content(
    persistence: PersistencePort,
    *,
    thread_id: str,
) -> str:
    """从流事件中恢复用户已经看见的文字，避免失败 Turn 变成空历史。"""

    stream = persistence.get_conversation_stream(thread_id)
    if stream is not None and stream.assistant_content.strip():
        return stream.assistant_content[-_RECOVERABLE_STREAM_CONTENT_CHARACTERS :]

    commentary: dict[str, str] = {}
    commentary_order: list[str] = []
    tokens: list[str] = []
    for event in persistence.list_conversation_stream_events(
        thread_id,
        after_sequence=0,
        limit=2_000,
    ):
        data = event.data
        if not isinstance(data, dict):
            continue
        if event.event == "commentary":
            group_id = str(
                data.get("commentary_id")
                or data.get("conversation_event_id")
                or event.sequence
            )
            if group_id not in commentary:
                commentary[group_id] = ""
                commentary_order.append(group_id)
            commentary[group_id] += str(data.get("token", data.get("content", "")))
        elif event.event == "token":
            tokens.append(str(data.get("token", data.get("content", ""))))

    visible_parts = [commentary[item] for item in commentary_order if commentary[item].strip()]
    if tokens:
        visible_parts.append("".join(tokens))
    return "\n\n".join(visible_parts)[-_RECOVERABLE_STREAM_CONTENT_CHARACTERS :]


def _failure_assistant_message(
    persistence: PersistencePort,
    *,
    thread_id: str,
    detail: str,
) -> str:
    previous = _recoverable_stream_content(persistence, thread_id=thread_id).strip()
    suffix = f"任务执行中断：{detail} 已保留此前进展，可以继续说明或重试。"
    return f"{previous}\n\n{suffix}".strip() if previous else suffix


class _ContinuousWorkbenchProjection:
    """Persist a compact live workbench view for the conversation-first Agent."""

    _OBSERVED_EVENTS = {
        "turn_status",
        "phase_changed",
        "tool_call",
        "tool_result",
        "approval",
        "result",
        "error",
        "done",
    }

    def __init__(
        self,
        persistence: PersistencePort,
        *,
        project_key: str,
        thread_id: str,
        task_text: str,
    ) -> None:
        self._persistence = persistence
        self._project_key = project_key
        self._thread_id = thread_id
        self._task_text = task_text
        self._status = "running"
        self._current_task_id: str | None = "agent_decision"
        self._tasks: dict[str, dict[str, object]] = {}
        self._evidence_ids: list[str] = []
        self._trace: list[str] = []
        self._recovery: list[dict[str, object]] = []
        existing = persistence.get_workbench_snapshot(project_key)
        if (
            existing is not None
            and existing.workflow_family == "continuous_agent"
            and existing.thread_id == thread_id
        ):
            snapshot = existing.snapshot
            self._status = str(snapshot.get("status", self._status))
            self._current_task_id = (
                str(snapshot["current_task_id"])
                if snapshot.get("current_task_id") is not None
                else None
            )
            self._tasks = {
                str(item.get("task_id")): dict(item)
                for item in snapshot.get("tasks", [])
                if isinstance(item, dict) and item.get("task_id")
            }
            self._evidence_ids = [
                str(item.get("evidence_id"))
                for item in snapshot.get("evidence", [])
                if isinstance(item, dict) and item.get("evidence_id")
            ]
            self._trace = [str(item) for item in snapshot.get("trace", [])]
            self._recovery = [
                dict(item)
                for item in snapshot.get("recovery", [])
                if isinstance(item, dict)
            ]

    def observe(self, event: str, data: dict[str, object] | str) -> None:
        if event not in self._OBSERVED_EVENTS:
            return
        payload = data if isinstance(data, dict) else {}
        self._trace.append(
            ":".join(
                item
                for item in (
                    event,
                    str(payload.get("phase") or payload.get("status") or ""),
                )
                if item
            )
        )
        self._trace = self._trace[-80:]

        if event == "turn_status":
            self._status = str(payload.get("status", self._status))
        elif event == "phase_changed":
            phase = str(payload.get("phase", "agent_decision"))
            self._current_task_id = phase
        elif event == "tool_call":
            call_id = str(
                payload.get("call_id")
                or payload.get("conversation_event_id")
                or payload.get("tool_call")
                or "tool"
            )
            tool_name = str(
                payload.get("tool_name") or payload.get("tool_call") or "工具"
            )
            self._tasks[call_id] = {
                "task_id": call_id,
                "parent_id": None,
                "kind": "tool",
                "title": tool_name,
                "description": f"执行 {tool_name}",
                "depends_on": [],
                "status": "running",
                "attempts": 1,
                "max_attempts": 2,
                "requires_approval": False,
                "allowed_tools": [tool_name],
                "acceptance_criteria": [],
            }
            self._current_task_id = call_id
        elif event == "tool_result":
            call_id = str(payload.get("call_id") or "tool")
            status = str(payload.get("status", "failed"))
            task = self._tasks.get(call_id)
            if task is not None:
                task["status"] = {
                    "succeeded": "passed",
                    "completed": "passed",
                    "rejected": "blocked",
                }.get(status, status)
            for evidence_id in payload.get("evidence_ids", []):
                evidence = str(evidence_id)
                if evidence and evidence not in self._evidence_ids:
                    self._evidence_ids.append(evidence)
            self._current_task_id = "agent_decision"
        elif event == "approval":
            self._status = "awaiting_user"
        elif event == "result":
            raw_status = str(payload.get("status", self._status))
            self._status = {
                "waiting_approval": "awaiting_user",
                "waiting_input": "awaiting_user",
            }.get(raw_status, raw_status)
            if self._status in {"completed", "failed", "cancelled"}:
                self._current_task_id = None
        elif event == "error":
            self._status = "failed"
            self._current_task_id = None
            self._recovery.append(
                {
                    "task_id": str(payload.get("call_id") or "agent_decision"),
                    "category": str(payload.get("category", "internal")),
                    "message": str(payload.get("message", "任务执行失败")),
                    "attempt": 1,
                    "repeated": False,
                }
            )
            self._recovery = self._recovery[-20:]
        self._save()

    def _save(self) -> None:
        terminal = self._status in {"completed", "failed", "cancelled"}
        criterion_status = (
            "passed"
            if self._status == "completed"
            else "failed"
            if self._status == "failed"
            else "pending"
        )
        root_status = (
            "passed"
            if self._status == "completed"
            else "failed"
            if self._status == "failed"
            else "blocked"
            if self._status in {"awaiting_user", "cancelled"}
            else "running"
        )
        root_task = {
            "task_id": "continuous_turn",
            "parent_id": None,
            "kind": "agent",
            "title": self._task_text[:240] or "持续 Agent 任务",
            "description": self._task_text[:8_000],
            "depends_on": [],
            "status": root_status,
            "attempts": 1,
            "max_attempts": 1,
            "requires_approval": self._status == "awaiting_user",
            "allowed_tools": [],
            "acceptance_criteria": ["完成用户请求并返回可验证结果"],
        }
        self._persistence.save_workbench_snapshot(
            project_key=self._project_key,
            workflow_family="continuous_agent",
            thread_id=self._thread_id,
            snapshot={
                "revision": 1,
                "status": self._status,
                "task_mode": "firmware",
                "supports_interactions": False,
                "objective": {
                    "objective_id": f"continuous:{self._thread_id}",
                    "title": self._task_text[:240] or "持续 Agent 任务",
                    "description": self._task_text[:8_000],
                    "status": (
                        "completed"
                        if self._status == "completed"
                        else "blocked"
                        if terminal
                        else "active"
                    ),
                    "priority": 50,
                    "acceptance_criteria": ["完成用户请求并返回可验证结果"],
                    "constraints": ["写操作必须经过明确审批"],
                    "revision": 1,
                },
                "changes": [],
                "tasks": [root_task, *self._tasks.values()],
                "capabilities": [],
                "acceptance": [
                    {
                        "criterion_id": f"continuous:{self._thread_id}:result",
                        "description": "完成用户请求并返回可验证结果",
                        "verification_kind": "continuous_agent_result",
                        "status": criterion_status,
                        "required_evidence": list(self._evidence_ids),
                        "evidence_ids": list(self._evidence_ids),
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": evidence_id,
                        "kind": evidence_id.partition(":")[0],
                        "accepted_by": [
                            f"continuous:{self._thread_id}:result"
                        ],
                    }
                    for evidence_id in self._evidence_ids
                ],
                "interactions": [],
                "recovery": list(self._recovery),
                "trace": list(self._trace),
                "current_task_id": self._current_task_id,
                "acceptance_passed": criterion_status == "passed",
                "build_verified": False,
                "hardware_function_verified": False,
                "blocked_reason": (
                    str(self._recovery[-1]["message"])
                    if self._recovery
                    else None
                ),
            },
        )


def _derive_history_events(
    persistence: PersistencePort,
    *,
    project_key: str,
    turn_id: str,
) -> list[ConversationEvent]:
    """把最近的历史对话派生为事件，供持续 Agent 跨轮继承上下文。

    只取 user/assistant 消息（跳过空内容），每条内容截断到尾部
    ``_HISTORY_CONTENT_CHARACTERS`` 字符（与专用知识图的 conversation_history
    截断策略一致），最多注入最近 ``_HISTORY_EVENT_LIMIT`` 条。结果写入
    ``conversation_history`` channel（每轮覆盖，不参与 events 的跨轮累积），
    因此不会触发 compact_context，也不会被投影到会话流或被误认为当前
    turn 的回复。
    """
    history = [
        item
        for item in persistence.get_messages(project_key)
        if str(item.get("role", "")) in {"user", "assistant"}
        and str(item.get("content", "")).strip()
    ][-_HISTORY_EVENT_LIMIT:]
    events: list[ConversationEvent] = []
    for index, item in enumerate(history):
        role = str(item.get("role", ""))
        events.append(
            ConversationEvent(
                event_id=f"history:{turn_id}:{index}",
                turn_id=f"history:{turn_id}",
                kind=(
                    "user_message"
                    if role == "user"
                    else "assistant_message"
                ),
                sequence=index,
                payload={
                    "content": str(item.get("content", ""))[
                        -_HISTORY_CONTENT_CHARACTERS:
                    ]
                },
            )
        )
    return events


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
    """取当前 turn 的 assistant 回复，绝不回退到历史 turn 的内容。

    事件列表跨 turn 累积，若按“最后一条 assistant_message”取值，当前 turn
    因审批被拒/工具失败而未生成回复时，会错误复读上一个 turn 的旧回复
    （用户会看到答非所问的重复输出）。这里严格限定 turn_id，取不到时按
    turn 状态生成语义化兜底消息。
    """

    state = result.state
    turn_id = state.get("turn_id")
    if turn_id:
        for event in reversed(state.get("events", [])):
            if event.kind == "assistant_message" and event.turn_id == turn_id:
                content = str(event.payload.get("content", "")).strip()
                if content:
                    return content
    if result.pending_approval is not None:
        # 审批请求已由 approval 事件/审批卡片呈现，这里不再生成与审批摘要
        # 内容相同的 assistant 消息，避免 UI 把“执行工具…”渲染成两条重复。
        return ""
    failure = state.get("last_failure")
    if failure is not None:
        return failure.message
    approvals = state.get("domain_approvals", {})
    feedback = state.get("domain_approval_feedback", {})
    if approvals and feedback:
        latest_call = next(
            (call for call in reversed(list(approvals)) if call in feedback),
            None,
        )
        if latest_call is not None and not approvals[latest_call]:
            note = str(feedback[latest_call]).strip()
            return (
                "你拒绝了该任务的审批，已保留现场并停止执行"
                + (f"；你的反馈：{note}" if note else "")
                + "。可以补充信息后让我继续。"
            )
    status = state.get("turn_status")
    objective_status = state.get("objective_status")
    if status in {"blocked", "failed"} or objective_status == "blocked":
        return "当前任务已阻塞并保留现场；可以补充信息后让我继续。"
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
        # 审批恢复在另一个 HTTP 请求中完成，必须从持久化配置恢复原始
        # user 消息，才能在最终终态写入完整且唯一的聊天 exchange。
        "task_text": message,
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
    history_events = _derive_history_events(
        persistence,
        project_key=project_key,
        turn_id=turn_id,
    )
    initial_state: dict[str, object] = {
        "session_id": session_id,
        "turn_id": turn_id,
        "project_key": project_key,
        "session_status": "active",
        "turn_status": "running",
        # events 每轮只含本轮：merge_conversation_events 检测到本轮 user
        # 消息会丢弃上一轮事件，工具证据不跨轮泄漏。
        "events": [
            ConversationEvent(
                event_id=f"{turn_id}:user",
                turn_id=turn_id,
                kind="user_message",
                sequence=0,
                payload={"content": message},
            )
        ],
        # events 已每轮重置，压缩游标与摘要也必须同步归零，否则旧 checkpoint
        # 残留的 compaction_cursor 会过滤掉本轮 user 消息（sequence 从 0 重新
        # 编号），残留的旧摘要也会继续误导模型决策。
        "compaction_cursor": 0,
        "context_summary": "",
        # 历史对话作为每轮输入（覆盖式 channel），decide_next_step 会把它
        # 与本轮事件一起交给模型。
        "conversation_history": history_events,
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
    workbench = _ContinuousWorkbenchProjection(
        persistence,
        project_key=project_key,
        thread_id=turn_id,
        task_text=message,
    )

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
        try:
            workbench.observe(event, data)
        except Exception:
            # 工作台是投影视图，持久化失败不能反向中断主任务。
            pass
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
            # 待审批仍属于当前进行中的 Turn。此时若提前写入历史，WebUI
            # 恢复时会同时渲染 messages 与 active_run.user_message；审批恢复
            # 完成后还会再次写入同一 exchange。只在真正终态保存聊天历史。
            if result.pending_approval is None:
                persistence.append_exchange(
                    project_key,
                    thread_id=turn_id,
                    user_message=message,
                    assistant_message=assistant_message,
                )
        except Exception as error:
            # 兜底桶：worker try 块内任何未捕获异常都收敛于此。历史教训
            # （oled4 反复"持续 Agent 执行失败"）：原始异常曾被丢弃，导致
            # 应用侧查不到根因。这里必须记录完整 traceback，并把异常原文
            # 写进 failure.details，随错误事件与终态记录持久化。
            _LOGGER.exception("持续 Agent Turn %s 异常终止", turn_id)
            failure = {
                "category": "runtime",
                "code": "continuous_agent_failed",
                "message": "持续 Agent 执行失败",
                "details": {
                    "error": f"{type(error).__name__}: {error}"[:2_000],
                    "turn_id": turn_id,
                },
            }
            failure_message = _failure_assistant_message(
                persistence,
                thread_id=turn_id,
                detail="持续 Agent 执行失败。",
            )
            publish("error", failure)
            publish("done", "[DONE]")
            persistence.finish_conversation_stream(turn_id, status="failed")
            persistence.finish_run(turn_id, status="failed", result=failure)
            persistence.finish_agent_turn(
                turn_id,
                status="failed",
                assistant_message=failure_message,
                failure=failure,
            )
            persistence.append_exchange(
                project_key,
                thread_id=turn_id,
                user_message=message,
                assistant_message=failure_message,
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
    workbench = _ContinuousWorkbenchProjection(
        persistence,
        project_key=record.task_key,
        thread_id=turn_id,
        task_text=str(record.runtime_config.get("task_text", "")),
    )
    stream = persistence.get_conversation_stream(turn_id)
    if stream is not None:
        persistence.finish_conversation_stream(turn_id, status="running")
        resume_event = {"phase": "approval_resume", "message": "审批已处理，正在继续任务"}
        persistence.append_conversation_stream_event(
            turn_id,
            event="phase_changed",
            data=resume_event,
        )
        try:
            workbench.observe("phase_changed", resume_event)
        except Exception:
            pass

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
        try:
            workbench.observe(event, data)
        except Exception:
            pass

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
                persistence.finish_conversation_stream(
                    turn_id,
                    status=(
                        "failed"
                        if turn_status == "failed"
                        else "completed"
                    ),
                )
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
        except Exception as error:
            # 与主 worker 兜底桶同样的可观测性要求：原始异常不得丢失，
            # 否则用户只能看到"审批后的任务恢复失败"而查不到根因。
            _LOGGER.exception("持续 Agent 审批恢复 Turn %s 异常终止", turn_id)
            failure = {
                "category": "recovery",
                "code": "approval_resume_failed",
                "message": "审批后的任务恢复失败，已保留此前进展。",
                "details": {
                    "error": f"{type(error).__name__}: {error}"[:2_000],
                    "turn_id": turn_id,
                },
            }
            failure_message = _failure_assistant_message(
                persistence,
                thread_id=turn_id,
                detail="审批后的任务恢复失败。",
            )
            publish("error", failure)
            publish("result", {"status": "failed", "failure": failure})
            publish("done", "[DONE]")
            persistence.finish_conversation_stream(turn_id, status="failed")
            persistence.finish_run(turn_id, status="failed", result=failure)
            persistence.finish_agent_turn(
                turn_id,
                status="failed",
                assistant_message=failure_message,
                failure=failure,
            )
            persistence.complete_approval(record.task_key, failed=True)
            persistence.append_exchange(
                record.task_key,
                thread_id=turn_id,
                user_message=str(record.runtime_config.get("task_text", "")),
                assistant_message=failure_message,
            )
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
