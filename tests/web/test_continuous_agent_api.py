from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from luxar.application.continuous_agent_graph import ContinuousAgentRuntimeContext
from luxar.application.domain_workflow_registry import DomainWorkflowRegistry
from luxar.application.tool_registry import ToolRegistry
from luxar.database import TransientPersistence
from luxar.database.local_runtime import LocalStorageRuntime
from luxar.database.local_settings import LocalStorageSettings
from luxar.domain.continuous_agent.steps import (
    AgentStepContext,
    AgentToolDescriptor,
    AskUser,
    AssistantReply,
    DomainWorkflowCall,
    ToolCall,
    ToolCallBatch,
)
from luxar.domain.continuous_agent.requests import (
    MissingInputRequest,
    ToolApprovalRequest,
)
from luxar.domain.continuous_agent.tools import ToolResult
from luxar.domain.conversation import ConversationDecision
from luxar.ports.agent_tool import AgentToolExecutionContext
from luxar.ports.domain_workflow import (
    DomainWorkflowDescriptor,
    DomainWorkflowExecutionContext,
    DomainWorkflowOutcome,
)
from luxar.web import create_app


def _make_project(root: Path, name: str = "test4") -> None:
    project = root / name
    project.mkdir()
    (project / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n"
        f"project({name})\n",
        encoding="utf-8",
    )


def _sse_text(payload: str) -> str:
    parts: list[str] = []
    for frame in payload.strip().split("\n\n"):
        lines = frame.splitlines()
        if not lines or lines[0] != "event: token":
            continue
        data = json.loads(lines[1].removeprefix("data: "))
        parts.append(str(data["token"]))
    return "".join(parts)


def _wait_until(predicate: object, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not satisfied before timeout")


class _ReplyStepper:
    def __init__(self) -> None:
        self.contexts: list[AgentStepContext] = []

    def decide_next_step(self, context: AgentStepContext) -> AssistantReply:
        self.contexts.append(context)
        message = next(
            str(event.payload["content"])
            for event in reversed(context.recent_events)
            if event.kind == "user_message"
        )
        return AssistantReply(content=f"收到：{message}")


class _NaturalCommentaryReplyStepper(_ReplyStepper):
    def decide_next_step_streaming(
        self,
        context: AgentStepContext,
        *,
        on_commentary: object,
    ) -> AssistantReply:
        assert callable(on_commentary)
        self.contexts.append(context)
        on_commentary("我先看一下工程当前的实现，")
        on_commentary("确认问题落在哪一层。")
        return AssistantReply(content="工程检查完成。")


class _ContextFactory:
    def __init__(self, stepper: _ReplyStepper | None = None) -> None:
        self.stepper = stepper or _ReplyStepper()

    def __call__(self, **kwargs: object) -> ContinuousAgentRuntimeContext:
        return ContinuousAgentRuntimeContext(
            stepper=self.stepper,
            tools=ToolRegistry(),
            project_path=kwargs["project_path"],  # type: ignore[arg-type]
        )


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _ApprovalTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="device.flash",
        description="测试审批工具",
        input_schema=_NoArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments, context
        self.calls += 1
        return ToolResult(
            success=True,
            output={"flashed": True},
            evidence_ids=["flash:test"],
        )


class _ReadOnlyTool(_ApprovalTool):
    descriptor = AgentToolDescriptor(
        name="workspace.inspect",
        description="测试只读工具",
        input_schema=_NoArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )


class _BlockingBoundaryStepper:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.contexts: list[AgentStepContext] = []

    def decide_next_step(
        self,
        context: AgentStepContext,
    ) -> ToolCallBatch | AssistantReply:
        self.contexts.append(context)
        if not context.latest_tool_results:
            self.entered.set()
            assert self.release.wait(timeout=5)
            return ToolCallBatch(
                calls=[
                    ToolCall(
                        call_id="inspect-boundary",
                        tool_name="workspace.inspect",
                        arguments={},
                    )
                ]
            )
        steering = [
            str(event.payload["content"])
            for event in context.recent_events
            if event.payload.get("steering") is True
        ]
        return AssistantReply(content=f"已接受运行中指令：{steering[-1]}")


class _PerTurnToolStepper:
    def __init__(self) -> None:
        self.second_turn_tool_results: list[dict[str, object]] | None = None

    def decide_next_step(
        self,
        context: AgentStepContext,
    ) -> ToolCallBatch | AssistantReply:
        message = next(
            str(event.payload["content"])
            for event in reversed(context.recent_events)
            if event.kind == "user_message"
        )
        if message == "第一轮调用工具":
            if not context.latest_tool_results:
                return ToolCallBatch(
                    calls=[
                        ToolCall(
                            call_id="inspect-first-turn",
                            tool_name="workspace.inspect",
                            arguments={},
                        )
                    ]
                )
            return AssistantReply(content="第一轮完成")
        self.second_turn_tool_results = list(context.latest_tool_results)
        return AssistantReply(content="第二轮直接回答")


class _ApprovalStepper:
    def decide_next_step(
        self,
        context: AgentStepContext,
    ) -> ToolCallBatch | AssistantReply:
        if context.latest_tool_results:
            return AssistantReply(content="烧录完成，设备工具只执行了一次。")
        return ToolCallBatch(
            calls=[
                ToolCall(
                    call_id="flash-once",
                    tool_name="device.flash",
                    arguments={},
                )
            ]
        )


class _SerialFollowupStepper:
    def __init__(self) -> None:
        self.saw_pending_request = False

    def decide_next_step(
        self,
        context: AgentStepContext,
    ) -> AskUser | ToolCallBatch | AssistantReply:
        if context.latest_tool_results:
            return AssistantReply(content="已使用 COM4 完成烧录。")
        if context.pending_request is not None:
            self.saw_pending_request = True
            latest_message = next(
                str(event.payload["content"])
                for event in reversed(context.recent_events)
                if event.kind == "user_message"
            )
            assert "COM4" in latest_message
            return ToolCallBatch(
                calls=[
                    ToolCall(
                        call_id="flash-after-serial",
                        tool_name="device.flash",
                        arguments={},
                    )
                ]
            )
        return AskUser(
            request=MissingInputRequest(
                request_id="serial-port",
                prompt="请连接开发板并告诉我串口号。",
                fields=["serial_port"],
                reason="烧录前需要目标串口",
            )
        )


class _DomainStepper:
    def decide_next_step(
        self,
        context: AgentStepContext,
    ) -> DomainWorkflowCall | AssistantReply:
        if context.latest_domain_results:
            result = context.latest_domain_results[-1]
            return AssistantReply(
                content=f"领域任务已回流顶层 Agent：{result['status']}"
            )
        return DomainWorkflowCall(
            call_id="complex-change",
            workflow_name="project.change",
            task="复杂修改并完成非回归验收",
        )


class _DomainWorkflow:
    descriptor = DomainWorkflowDescriptor(
        name="project.change",
        description="复杂项目变更",
    )

    def __init__(self) -> None:
        self.starts = 0
        self.resumes = 0

    def start(
        self,
        call: DomainWorkflowCall,
        context: DomainWorkflowExecutionContext,
    ) -> DomainWorkflowOutcome:
        self.starts += 1
        return DomainWorkflowOutcome(
            status="waiting_approval",
            summary="准备执行复杂代码变更",
            result={"planned": True},
            pending_approval=ToolApprovalRequest(
                request_id="domain:complex-change:edit",
                call_id=call.call_id,
                tool_name="project.change",
                summary="准备执行复杂代码变更",
                risk="write",
            ),
        )

    def resume(
        self,
        call: DomainWorkflowCall,
        context: DomainWorkflowExecutionContext,
        *,
        approved: bool,
        feedback: str = "",
    ) -> DomainWorkflowOutcome:
        self.resumes += 1
        assert approved is True
        return DomainWorkflowOutcome(
            status="completed",
            summary="复杂代码变更已完成",
            result={"acceptance_passed": True},
        )


def test_v2_direct_turns_reuse_session_and_expose_distinct_turns(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=_ContextFactory(),
        )
    )

    first = client.post(
        "/api/conversations/test4",
        json={"message": "你好", "client_turn_id": "browser-1"},
    )
    second = client.post(
        "/api/conversations/test4",
        json={"message": "继续", "client_turn_id": "browser-2"},
    )
    conversation = client.get("/api/conversations/test4")
    workbench = client.get("/api/projects/test4/agent")

    assert first.status_code == second.status_code == 200
    assert first.headers["X-LUXAR-Session-ID"] == second.headers[
        "X-LUXAR-Session-ID"
    ]
    assert first.headers["X-LUXAR-Turn-ID"] != second.headers[
        "X-LUXAR-Turn-ID"
    ]
    assert _sse_text(second.text) == "收到：继续"
    assert conversation.json()["continuous_agent_v2"] is True
    assert conversation.json()["session_id"] == first.headers[
        "X-LUXAR-Session-ID"
    ]
    assert workbench.status_code == 200
    assert workbench.json()["workflow_family"] == "continuous_agent"
    assert workbench.json()["status"] == "completed"
    assert workbench.json()["tasks"][0]["task_id"] == "continuous_turn"


def test_v2_stream_surfaces_commentary_before_the_final_answer(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=_ContextFactory(
                _NaturalCommentaryReplyStepper()
            ),
        )
    )

    response = client.post(
        "/api/conversations/test4",
        json={"message": "检查工程", "client_turn_id": "visible-progress-1"},
    )

    assert response.status_code == 200
    assert "event: commentary" in response.text
    assert response.text.index("event: commentary") < response.text.index("event: token")
    events = persistence.list_conversation_stream_events(
        response.headers["X-LUXAR-Turn-ID"],
        after_sequence=0,
        limit=100,
    )
    commentary = [item for item in events if item.event == "commentary"]
    assert "".join(str(item.data["token"]) for item in commentary) == (
        "我先看一下工程当前的实现，确认问题落在哪一层。"
    )
    assert all(item.data["phase"] == "commentary" for item in commentary)


def test_v2_duplicate_client_turn_replays_without_duplicate_history(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=_ContextFactory(),
        )
    )

    first = client.post(
        "/api/conversations/test4",
        json={"message": "你好", "client_turn_id": "same-request"},
    )
    replay = client.post(
        "/api/conversations/test4",
        json={
            "message": "这段内容不会覆盖原 Turn",
            "session_id": first.headers["X-LUXAR-Session-ID"],
            "client_turn_id": "same-request",
        },
    )

    assert replay.headers["X-LUXAR-Turn-ID"] == first.headers[
        "X-LUXAR-Turn-ID"
    ]
    assert _sse_text(replay.text) == "收到：你好"
    assert len(persistence.get_messages("0:test4")) == 2


def test_v2_workflow_uses_session_for_langgraph_and_turn_for_run(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    stepper = _ReplyStepper()

    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=_ContextFactory(stepper),
        )
    )

    first = client.post(
        "/api/conversations/test4",
        json={"message": "构建工程", "client_turn_id": "build-1"},
    )
    second = client.post(
        "/api/conversations/test4",
        json={"message": "再检查一次", "client_turn_id": "build-2"},
    )

    session_id = first.headers["X-LUXAR-Session-ID"]
    assert second.headers["X-LUXAR-Session-ID"] == session_id
    assert [context.session_id for context in stepper.contexts] == [
        session_id,
        session_id,
    ]
    assert first.headers["X-LUXAR-Turn-ID"] != session_id
    assert second.headers["X-LUXAR-Turn-ID"] != session_id
    latest = persistence.get_latest_run("0:test4")
    assert latest is not None
    assert latest.thread_id == second.headers["X-LUXAR-Turn-ID"]


def test_v2_tool_results_and_result_envelope_are_isolated_per_turn(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    stepper = _PerTurnToolStepper()
    registry = ToolRegistry([_ReadOnlyTool()])

    def context_factory(**kwargs: object) -> ContinuousAgentRuntimeContext:
        return ContinuousAgentRuntimeContext(
            stepper=stepper,
            tools=registry,
            project_path=kwargs["project_path"],  # type: ignore[arg-type]
        )

    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=context_factory,
        )
    )
    first = client.post(
        "/api/conversations/test4",
        json={"message": "第一轮调用工具", "client_turn_id": "tool-turn"},
    )
    second = client.post(
        "/api/conversations/test4",
        json={"message": "第二轮直接回答", "client_turn_id": "reply-turn"},
    )
    result_frames = [
        json.loads(frame.splitlines()[1].removeprefix("data: "))
        for frame in second.text.strip().split("\n\n")
        if frame.startswith("event: result")
    ]

    assert first.status_code == second.status_code == 200
    assert stepper.second_turn_tool_results == []
    assert result_frames[0]["tool_calls"] == {}
    assert result_frames[0]["evidence_ids"] == []


def test_v2_can_archive_current_session_and_start_a_new_one(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=_ContextFactory(),
        )
    )
    first = client.post(
        "/api/conversations/test4",
        json={"message": "你好", "client_turn_id": "turn-before-reset"},
    )
    old_session_id = first.headers["X-LUXAR-Session-ID"]

    created = client.post("/api/conversations/test4/sessions")
    current = client.get("/api/conversations/test4/session")

    assert created.status_code == 200
    assert created.json()["archived_session_id"] == old_session_id
    assert created.json()["session_id"] != old_session_id
    assert current.json()["session"]["session_id"] == created.json()[
        "session_id"
    ]
    assert persistence.get_agent_session(old_session_id).status == "archived"  # type: ignore[union-attr]


def test_v2_reset_preserves_turn_audit_while_rotating_session(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=_ContextFactory(),
        )
    )
    first = client.post(
        "/api/conversations/test4",
        json={"message": "你好", "client_turn_id": "audited-turn"},
    )
    turn_id = first.headers["X-LUXAR-Turn-ID"]

    reset = client.post("/api/conversations/test4/reset")

    assert reset.status_code == 200
    assert reset.json()["session_id"] != first.headers["X-LUXAR-Session-ID"]
    assert persistence.get_agent_turn(turn_id) is not None
    assert persistence.get_messages("0:test4") == []


def test_v2_web_approval_resumes_same_graph_and_executes_tool_once(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    tool = _ApprovalTool()
    context = ContinuousAgentRuntimeContext(
        stepper=_ApprovalStepper(),
        tools=ToolRegistry([tool], ledger=persistence),
        project_path=tmp_path / "test4",
    )
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=lambda **_: context,
        )
    )

    waiting = client.post(
        "/api/conversations/test4",
        json={"message": "烧录开发板", "client_turn_id": "flash-request"},
    )

    assert waiting.status_code == 200
    assert "event: approval" in waiting.text
    assert tool.calls == 0
    approval = persistence.get_pending_approval("0:test4")
    assert approval is not None
    assert approval.thread_id == waiting.headers["X-LUXAR-Session-ID"]
    # 待审批仍是进行中的同一 Turn，不能提前进入已完成聊天历史；否则
    # WebUI 会同时渲染 messages 与 active_run.user_message，显示两次输入。
    assert persistence.get_messages("0:test4") == []
    waiting_conversation = client.get("/api/conversations/test4").json()
    assert waiting_conversation["messages"] == []
    assert waiting_conversation["active_run"]["user_message"] == "烧录开发板"
    assert waiting_conversation["active_run"]["status"] == "pending_approval"

    resumed = client.post(
        "/api/conversations/test4/approval",
        json={"decision": "approve", "feedback": "已确认连接 COM4"},
    )
    repeated = client.post(
        "/api/conversations/test4/approval",
        json={"decision": "approve"},
    )

    assert resumed.status_code == 200
    assert resumed.json()["status"] == "resuming"
    assert resumed.json()["turn_id"] == waiting.headers["X-LUXAR-Turn-ID"]
    _wait_until(lambda: tool.calls == 1)
    _wait_until(
        lambda: len(persistence.get_messages("0:test4")) == 2
    )
    assert tool.calls == 1
    assert persistence.get_messages("0:test4") == [
        {"role": "user", "content": "烧录开发板"},
        {
            "role": "assistant",
            "content": "烧录完成，设备工具只执行了一次。",
        },
    ]
    result_events = [
        item.data
        for item in persistence.list_conversation_stream_events(
            waiting.headers["X-LUXAR-Turn-ID"],
            after_sequence=0,
            limit=100,
        )
        if item.event == "result"
    ]
    assert result_events[-1]["evidence_ids"] == ["flash:test"]
    event_names = [
        item.event
        for item in persistence.list_conversation_stream_events(
            waiting.headers["X-LUXAR-Turn-ID"],
            after_sequence=0,
            limit=100,
        )
    ]
    assert event_names.count("tool_call") == 1
    assert event_names.count("tool_result") == 1
    assert repeated.status_code == 409
    assert tool.calls == 1


def test_v2_natural_followup_resolves_prior_missing_input_without_retry_keyword(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    tool = _ApprovalTool()
    stepper = _SerialFollowupStepper()
    context = ContinuousAgentRuntimeContext(
        stepper=stepper,
        tools=ToolRegistry([tool], ledger=persistence),
        project_path=tmp_path / "test4",
    )
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=lambda **_: context,
        )
    )

    missing = client.post(
        "/api/conversations/test4",
        json={"message": "请烧录固件", "client_turn_id": "need-port"},
    )
    followup = client.post(
        "/api/conversations/test4",
        json={
            "message": "串口连接好了，是 COM4",
            "session_id": missing.headers["X-LUXAR-Session-ID"],
            "client_turn_id": "port-connected",
        },
    )

    assert missing.status_code == followup.status_code == 200
    assert _sse_text(missing.text) == "请连接开发板并告诉我串口号。"
    assert followup.headers["X-LUXAR-Session-ID"] == missing.headers[
        "X-LUXAR-Session-ID"
    ]
    assert "event: approval" in followup.text
    assert stepper.saw_pending_request is True
    assert tool.calls == 0

    approved = client.post(
        "/api/conversations/test4/approval",
        json={"decision": "approve", "feedback": "确认 COM4"},
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "resuming"
    _wait_until(lambda: tool.calls == 1)
    assert tool.calls == 1
    turn = persistence.get_agent_turn(approved.json()["turn_id"])
    assert turn is not None
    _wait_until(
        lambda: (
            persistence.get_agent_turn(approved.json()["turn_id"]).status
            == "completed"
        )
    )
    assert persistence.get_agent_turn(
        approved.json()["turn_id"]
    ).assistant_message == "已使用 COM4 完成烧录。"


def test_v2_web_complex_change_returns_domain_result_to_top_agent(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    workflow = _DomainWorkflow()
    context = ContinuousAgentRuntimeContext(
        stepper=_DomainStepper(),
        tools=ToolRegistry(),
        domain_workflows=DomainWorkflowRegistry([workflow]),
        project_path=tmp_path / "test4",
    )
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=lambda **_: context,
        )
    )

    waiting = client.post(
        "/api/conversations/test4",
        json={"message": "做一个复杂工程修改", "client_turn_id": "change-1"},
    )
    resumed = client.post(
        "/api/conversations/test4/approval",
        json={"decision": "approve", "feedback": "按计划执行"},
    )

    assert waiting.status_code == 200
    assert "event: approval" in waiting.text
    assert workflow.starts == 1
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "resuming"
    _wait_until(lambda: workflow.resumes == 1)
    _wait_until(
        lambda: persistence.get_agent_turn(resumed.json()["turn_id"]).status
        == "completed"
    )
    assert persistence.get_agent_turn(
        resumed.json()["turn_id"]
    ).assistant_message == (
        "领域任务已回流顶层 Agent：completed"
    )
    assert workflow.resumes == 1


def test_v2_sqlite_restart_recovers_approval_without_repeating_side_effect(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    _make_project(projects)
    settings = LocalStorageSettings(directory=tmp_path / "state")
    executions: list[str] = []

    class RestartSafeTool(_ApprovalTool):
        def execute(
            self,
            arguments: BaseModel,
            context: AgentToolExecutionContext,
        ) -> ToolResult:
            executions.append(context.turn_id)
            return ToolResult(
                success=True,
                output={"flashed": True},
                evidence_ids=["flash:restart-safe"],
            )

    def context_factory(**kwargs: object) -> ContinuousAgentRuntimeContext:
        persistence = kwargs["persistence"]
        return ContinuousAgentRuntimeContext(
            stepper=_ApprovalStepper(),
            tools=ToolRegistry(
                [RestartSafeTool()],
                ledger=persistence,  # type: ignore[arg-type]
            ),
            project_path=kwargs["project_path"],  # type: ignore[arg-type]
        )

    first_runtime = LocalStorageRuntime(settings)
    with TestClient(
        create_app(
            projects_roots=[projects],
            storage_runtime=first_runtime,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=context_factory,
        )
    ) as first_client:
        waiting = first_client.post(
            "/api/conversations/test4",
            json={"message": "烧录并等待审批", "client_turn_id": "restart-flash"},
        )
        turn_id = waiting.headers["X-LUXAR-Turn-ID"]
        assert "event: approval" in waiting.text
        assert executions == []

    second_runtime = LocalStorageRuntime(settings)
    with TestClient(
        create_app(
            projects_roots=[projects],
            storage_runtime=second_runtime,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=context_factory,
        )
    ) as second_client:
        resumed = second_client.post(
            "/api/conversations/test4/approval",
            json={"decision": "approve", "feedback": "重启后继续"},
        )
        repeated = second_client.post(
            "/api/conversations/test4/approval",
            json={"decision": "approve"},
        )
        _wait_until(
            lambda: second_runtime.persistence.get_agent_turn(turn_id).status
            == "completed"
        )
        result_events = [
            item.data
            for item in second_runtime.persistence.list_conversation_stream_events(
                turn_id, after_sequence=0, limit=100
            )
            if item.event == "result"
        ]

    assert resumed.status_code == 200
    assert resumed.json()["turn_id"] == turn_id
    assert resumed.json()["status"] == "resuming"
    _wait_until(lambda: executions == [turn_id])
    assert result_events[-1]["evidence_ids"] == ["flash:restart-safe"]
    assert repeated.status_code == 409
    assert executions == [turn_id]


def test_v2_sqlite_restart_restores_waiting_input_context(
    tmp_path: Path,
) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    _make_project(projects)
    settings = LocalStorageSettings(directory=tmp_path / "state")

    class RestartInputStepper:
        def decide_next_step(
            self,
            context: AgentStepContext,
        ) -> AskUser | AssistantReply:
            if context.pending_request is not None:
                latest = next(
                    str(event.payload["content"])
                    for event in reversed(context.recent_events)
                    if event.kind == "user_message"
                )
                return AssistantReply(
                    content=f"重启后已续接原请求：{latest}"
                )
            return AskUser(
                request=MissingInputRequest(
                    request_id="serial-after-restart",
                    prompt="请提供串口号。",
                    fields=["serial_port"],
                    reason="烧录需要串口",
                )
            )

    def context_factory(**kwargs: object) -> ContinuousAgentRuntimeContext:
        return ContinuousAgentRuntimeContext(
            stepper=RestartInputStepper(),
            tools=ToolRegistry(),
            project_path=kwargs["project_path"],  # type: ignore[arg-type]
        )

    with TestClient(
        create_app(
            projects_roots=[projects],
            storage_runtime=LocalStorageRuntime(settings),
            continuous_agent_enabled=True,
            continuous_agent_context_factory=context_factory,
        )
    ) as first_client:
        missing = first_client.post(
            "/api/conversations/test4",
            json={"message": "请烧录", "client_turn_id": "need-input"},
        )
        session_id = missing.headers["X-LUXAR-Session-ID"]

    with TestClient(
        create_app(
            projects_roots=[projects],
            storage_runtime=LocalStorageRuntime(settings),
            continuous_agent_enabled=True,
            continuous_agent_context_factory=context_factory,
        )
    ) as second_client:
        continued = second_client.post(
            "/api/conversations/test4",
            json={
                "message": "已经接好，是 COM4",
                "session_id": session_id,
                "client_turn_id": "input-after-restart",
            },
        )

    assert missing.status_code == continued.status_code == 200
    assert _sse_text(missing.text) == "请提供串口号。"
    assert continued.headers["X-LUXAR-Session-ID"] == session_id
    assert _sse_text(continued.text) == "重启后已续接原请求：已经接好，是 COM4"


def test_v2_accepts_steering_while_turn_is_running(tmp_path: Path) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    stepper = _BlockingBoundaryStepper()
    tool = _ReadOnlyTool()

    def context_factory(**kwargs: object) -> ContinuousAgentRuntimeContext:
        return ContinuousAgentRuntimeContext(
            stepper=stepper,
            tools=ToolRegistry([tool]),
            project_path=kwargs["project_path"],  # type: ignore[arg-type]
            drain_steering=kwargs.get("drain_steering"),  # type: ignore[arg-type]
            cancellation_requested=kwargs.get(  # type: ignore[arg-type]
                "cancellation_requested"
            ),
        )

    app = create_app(
        projects_roots=[tmp_path],
        persistence=persistence,
        continuous_agent_enabled=True,
        continuous_agent_context_factory=context_factory,
    )
    client = TestClient(app)
    responses: list[object] = []

    def run_turn() -> None:
        responses.append(
            client.post(
                "/api/conversations/test4",
                json={"message": "先检查工程", "client_turn_id": "running-1"},
            )
        )

    worker = threading.Thread(target=run_turn)
    worker.start()
    assert stepper.entered.wait(timeout=5)
    session_id = next(iter(app.state.continuous_active_sessions))
    turn_id = str(app.state.continuous_active_sessions[session_id]["turn_id"])
    early_events = persistence.list_conversation_stream_events(
        turn_id, after_sequence=0, limit=20
    )
    assert early_events[0].event == "turn_status"
    assert early_events[0].data["message"] == "正在理解需求"

    steering = client.post(
        "/api/conversations/test4/steer",
        json={
            "message": "改用 COM4 并继续",
            "client_steering_id": "steering-1",
            "session_id": session_id,
        },
    )
    duplicate_turn = client.post(
        "/api/conversations/test4",
        json={
            "message": "这是普通并发 Turn",
            "session_id": session_id,
            "client_turn_id": "running-duplicate",
        },
    )
    stepper.release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert steering.status_code == 202
    assert steering.json()["status"] == "queued"
    assert duplicate_turn.status_code == 409
    response = responses[0]
    assert response.status_code == 200  # type: ignore[union-attr]
    assert _sse_text(response.text) == (  # type: ignore[union-attr]
        "已接受运行中指令：改用 COM4 并继续"
    )
    assert tool.calls == 1


def test_v2_cancels_before_next_tool_side_effect(tmp_path: Path) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    stepper = _BlockingBoundaryStepper()
    tool = _ReadOnlyTool()

    def context_factory(**kwargs: object) -> ContinuousAgentRuntimeContext:
        return ContinuousAgentRuntimeContext(
            stepper=stepper,
            tools=ToolRegistry([tool]),
            project_path=kwargs["project_path"],  # type: ignore[arg-type]
            drain_steering=kwargs.get("drain_steering"),  # type: ignore[arg-type]
            cancellation_requested=kwargs.get(  # type: ignore[arg-type]
                "cancellation_requested"
            ),
        )

    app = create_app(
        projects_roots=[tmp_path],
        persistence=persistence,
        continuous_agent_enabled=True,
        continuous_agent_context_factory=context_factory,
    )
    client = TestClient(app)
    responses: list[object] = []

    def run_turn() -> None:
        responses.append(
            client.post(
                "/api/conversations/test4",
                json={"message": "检查并继续", "client_turn_id": "cancel-1"},
            )
        )

    worker = threading.Thread(target=run_turn)
    worker.start()
    assert stepper.entered.wait(timeout=5)
    session_id = next(iter(app.state.continuous_active_sessions))
    cancellation = client.post(
        "/api/conversations/test4/cancel",
        json={"session_id": session_id},
    )
    stepper.release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert cancellation.status_code == 202
    assert cancellation.json()["status"] == "cancellation_requested"
    response = responses[0]
    assert response.status_code == 200  # type: ignore[union-attr]
    assert _sse_text(response.text) == (  # type: ignore[union-attr]
        "已在安全边界停止当前任务，会话仍可继续。"
    )
    assert '"status":"cancelled"' in response.text  # type: ignore[union-attr]
    assert tool.calls == 0


def test_v2_marks_turn_failed_when_runtime_setup_aborts(tmp_path: Path) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()

    def broken_context_factory(**kwargs: object) -> ContinuousAgentRuntimeContext:
        del kwargs
        raise ValueError("测试 runtime 初始化失败")

    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=broken_context_factory,
        )
    )
    response = client.post(
        "/api/conversations/test4",
        json={"message": "开始任务", "client_turn_id": "runtime-abort"},
    )

    session = persistence.get_active_agent_session("0:test4")
    assert response.status_code == 409
    assert session is not None
    turn = persistence.get_agent_turn_by_client_id(
        session_id=session.session_id,
        client_turn_id="runtime-abort",
    )
    assert turn is not None
    assert turn.status == "failed"
    assert turn.failure is not None
    assert turn.failure["code"] == "turn_aborted_before_projection"


def test_v2_retired_project_allowlist_no_longer_creates_a_gray_route(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _make_project(tmp_path, "test4")
    _make_project(tmp_path, "legacy")
    monkeypatch.setenv(  # type: ignore[attr-defined]
        "LUXAR_CONTINUOUS_AGENT_V2_PROJECTS",
        "test4",
    )
    app = create_app(
        projects_roots=[tmp_path],
        persistence=TransientPersistence(),
        continuous_agent_enabled=True,
        continuous_agent_context_factory=_ContextFactory(),
    )
    client = TestClient(app)

    enabled = client.get("/api/conversations/test4")
    disabled = client.get("/api/conversations/legacy")
    runtime = client.get("/api/runtime")

    assert enabled.json()["continuous_agent_v2"] is True
    assert enabled.json()["continuous_agent_mode"] == "enabled"
    assert disabled.json()["continuous_agent_v2"] is True
    assert disabled.json()["continuous_agent_mode"] == "enabled"
    assert runtime.json()["continuous_agent_rollout"]["enabled_projects"] == []


def test_v2_retired_shadow_setting_respects_global_emergency_disable(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _make_project(tmp_path)
    monkeypatch.setenv(  # type: ignore[attr-defined]
        "LUXAR_CONTINUOUS_AGENT_V2_SHADOW_PROJECTS",
        "test4",
    )
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=TransientPersistence(),
            continuous_agent_enabled=False,
        )
    )

    conversation = client.get("/api/conversations/test4")
    session = client.get("/api/conversations/test4/session")

    assert conversation.json()["continuous_agent_v2"] is False
    assert conversation.json()["continuous_agent_mode"] == "disabled"
    assert session.json() == {"enabled": False, "mode": "disabled", "session": None}


def test_v2_retired_shadow_setting_does_not_run_a_hidden_model_call(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    _make_project(tmp_path)
    monkeypatch.setenv(  # type: ignore[attr-defined]
        "LUXAR_CONTINUOUS_AGENT_V2_SHADOW_PROJECTS",
        "test4",
    )
    persistence = TransientPersistence()
    shadow_stepper = _ReplyStepper()

    class CasualRouter:
        def route(
            self,
            message: str,
            history: list[dict[str, str]],
            knowledge_status: str | None = None,
            previous_run: dict[str, object] | None = None,
        ) -> ConversationDecision:
            del message, history, knowledge_status, previous_run
            return ConversationDecision(
                intent="casual_chat",
                response="旧入口正常回答。",
            )

    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=False,
            continuous_agent_context_factory=_ContextFactory(shadow_stepper),
            conversation_router=CasualRouter(),  # type: ignore[arg-type]
        )
    )
    response = client.post(
        "/api/conversations/test4",
        json={"message": "你好", "client_turn_id": "shadow-turn-1"},
    )

    observations = [
        item
        for item in persistence.get_agent_interactions("0:test4")
        if item.kind == "continuous_agent_shadow_decision"
    ]
    summary = client.get("/api/conversations/test4/shadow")
    assert response.status_code == 200
    assert _sse_text(response.text) == "旧入口正常回答。"
    assert len(shadow_stepper.contexts) == 0
    assert observations == []
    assert summary.status_code == 200
    assert summary.json()["summary"] == {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "broadly_compatible": 0,
        "broad_disagreements": 0,
        "disagreement_rate": 0.0,
    }


def test_v2_previous_exchanges_are_inherited_into_next_turn_context(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    stepper = _ReplyStepper()
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=_ContextFactory(stepper),
        )
    )

    first = client.post(
        "/api/conversations/test4",
        json={"message": "读取PDF并提取SH1106知识", "client_turn_id": "t1"},
    )
    second = client.post(
        "/api/conversations/test4",
        json={"message": "把前面提取的知识写入知识库", "client_turn_id": "t2"},
    )
    assert _sse_text(first.text) == "收到：读取PDF并提取SH1106知识"
    assert _sse_text(second.text) == "收到：把前面提取的知识写入知识库"

    second_context = stepper.contexts[-1]
    inherited = [
        (event.kind, str(event.payload.get("content", "")))
        for event in second_context.recent_events
    ]
    # 本轮 user 消息始终是最后一条
    assert inherited[-1] == ("user_message", "把前面提取的知识写入知识库")
    # 第一轮的 user/assistant 交换被派生为历史事件，跨轮继承
    assert ("user_message", "读取PDF并提取SH1106知识") in inherited
    assert ("assistant_message", "收到：读取PDF并提取SH1106知识") in inherited
    # 历史事件带独立 history: turn_id，不会冒充当前轮次
    second_turn_id = second.headers["X-LUXAR-Turn-ID"]
    history_ids = {
        event.event_id
        for event in second_context.recent_events
        if event.turn_id.startswith("history:")
    }
    assert history_ids == {
        f"history:{second_turn_id}:0",
        f"history:{second_turn_id}:1",
    }
    # 历史事件不会投影/发布到当前会话流
    stream_ids = [
        str(item.data.get("conversation_event_id"))
        for item in persistence.list_conversation_stream_events(
            second_turn_id,
            after_sequence=0,
        )
        if isinstance(item.data, dict)
        and item.data.get("conversation_event_id") is not None
    ]
    assert not any(item.startswith("history:") for item in stream_ids)


def test_v2_history_injection_is_bounded_and_truncated(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    stepper = _ReplyStepper()
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=_ContextFactory(stepper),
        )
    )
    # 预置 25 轮历史（每轮 user+assistant，assistant 超长），只应注入最近
    # 20 条消息且每条内容截断到末尾 2000 字符。
    for index in range(25):
        persistence.append_exchange(
            "0:test4",
            thread_id=f"seed-{index}",
            user_message=f"历史问题{index}",
            assistant_message=f"历史回答{index}" + "X" * 5_000,
        )

    client.post(
        "/api/conversations/test4",
        json={"message": "现在继续", "client_turn_id": "t-limit"},
    )
    events = stepper.contexts[-1].recent_events
    assert len(events) == 21  # 20 条历史 + 本轮 user
    assert str(events[0].payload["content"]) == "历史问题15"
    assert str(events[-1].payload["content"]) == "现在继续"
    assistant_contents = [
        str(event.payload["content"])
        for event in events
        if event.kind == "assistant_message"
    ]
    assert len(assistant_contents) == 10
    assert all(content == "X" * 2_000 for content in assistant_contents)


def test_v2_events_reset_per_turn_keeps_context_bounded_and_uncompacted(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    persistence = TransientPersistence()
    stepper = _ReplyStepper()
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=_ContextFactory(stepper),
        )
    )
    messages = [
        "读取PDF提取SH1106知识",
        "把知识写入知识库",
        "现在编写OLED的I2C驱动库，GPIO21=SDA，GPIO22=SCL",
    ]
    for index, message in enumerate(messages):
        response = client.post(
            "/api/conversations/test4",
            json={"message": message, "client_turn_id": f"t{index}"},
        )
        assert response.status_code == 200

    final = stepper.contexts[-1]
    # 未触发压缩：events 每轮重置，数量达不到压缩阈值，摘要必须为空
    assert final.context_summary == ""
    # 事件有界且不跨轮累积：注入的历史（前两轮 4 条消息）+ 本轮 user
    assert len(final.recent_events) == 5, len(final.recent_events)
    latest = [
        str(event.payload["content"])
        for event in final.recent_events
        if event.kind == "user_message"
    ]
    # 本轮新命令必须是模型看到的最新 user 消息（修复前它会被旧摘要/旧游标
    # 挤掉，导致 agent 反复回复"知识库已完成"而不执行）
    assert latest[-1] == messages[-1], latest
    assert messages[0] in latest  # 跨轮历史仍被继承


class _ExplodingWorkflow:
    """start 直接抛异常：异常从 execute_domain_workflow 节点冒出到 worker 兜底桶。"""

    descriptor = DomainWorkflowDescriptor(
        name="project.change",
        description="复杂项目变更",
    )

    def start(
        self,
        call: DomainWorkflowCall,
        context: DomainWorkflowExecutionContext,
    ) -> DomainWorkflowOutcome:
        del call, context
        raise RuntimeError("workflow exploded for observability test")

    def resume(
        self,
        call: DomainWorkflowCall,
        context: DomainWorkflowExecutionContext,
        *,
        approved: bool,
        feedback: str = "",
    ) -> DomainWorkflowOutcome:
        del call, context, approved, feedback
        raise RuntimeError("workflow exploded for observability test")


class _DomainWorkflowExploderStepper:
    def decide_next_step(self, context: AgentStepContext) -> DomainWorkflowCall:
        del context
        return DomainWorkflowCall(
            call_id="change-1",
            workflow_name="project.change",
            task="触发一次未捕获异常",
        )


def test_v2_worker_catch_all_preserves_original_error_in_details(
    tmp_path: Path,
) -> None:
    """Fix C：worker 兜底桶必须把原始异常写进 failure.details，便于定位根因。"""
    _make_project(tmp_path)
    persistence = TransientPersistence()
    context = ContinuousAgentRuntimeContext(
        stepper=_DomainWorkflowExploderStepper(),  # type: ignore[arg-type]
        tools=ToolRegistry(),
        domain_workflows=DomainWorkflowRegistry([_ExplodingWorkflow()]),
        project_path=tmp_path / "test4",
    )
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            persistence=persistence,
            continuous_agent_enabled=True,
            continuous_agent_context_factory=lambda **_: context,
        )
    )

    response = client.post(
        "/api/conversations/test4",
        json={"message": "执行会失败的领域工作流", "client_turn_id": "boom-1"},
    )
    assert response.status_code == 200
    assert "event: error" in response.text

    session = persistence.get_active_agent_session("0:test4")
    assert session is not None
    turn = persistence.get_agent_turn_by_client_id(
        session_id=session.session_id,
        client_turn_id="boom-1",
    )
    assert turn is not None
    assert turn.status == "failed"
    assert turn.failure is not None
    assert turn.failure["code"] == "continuous_agent_failed"
    assert "details" in turn.failure
    assert "RuntimeError" in turn.failure["details"]["error"]
    assert "workflow exploded" in turn.failure["details"]["error"]
