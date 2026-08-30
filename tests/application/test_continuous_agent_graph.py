from __future__ import annotations

import threading

from pydantic import BaseModel, ConfigDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from luxar.application.continuous_agent_graph import (
    ContinuousAgentRuntimeContext,
    build_continuous_agent_graph,
)
from luxar.application.domain_workflow_registry import DomainWorkflowRegistry
from luxar.application.tool_registry import ToolRegistry
from luxar.application.continuous_agent_steering import SteeringMessage
from luxar.domain.continuous_agent.events import ConversationEvent
from luxar.domain.continuous_agent.requests import (
    MissingInputRequest,
    ToolApprovalRequest,
)
from luxar.domain.continuous_agent.steps import (
    AgentStep,
    AgentStepContext,
    AgentToolDescriptor,
    AskUser,
    AssistantReply,
    DomainWorkflowCall,
    FinishObjective,
    ToolCall,
    ToolCallBatch,
)
from luxar.domain.continuous_agent.tools import ToolResult
from luxar.ports.agent_tool import AgentToolExecutionContext
from luxar.ports.domain_workflow import (
    DomainWorkflowDescriptor,
    DomainWorkflowExecutionContext,
    DomainWorkflowOutcome,
)
from luxar.ports.errors import CapabilityError


class _ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str


class _ReadTool:
    input_model = _ReadInput
    descriptor = AgentToolDescriptor(
        name="workspace.read",
        description="读取项目文件",
        input_schema=_ReadInput.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        self.calls += 1
        assert isinstance(arguments, _ReadInput)
        return ToolResult(
            success=True,
            output={"path": arguments.path, "content": "void app_main(void) {}"},
            evidence_ids=["source:main/main.c"],
        )


class _ApprovalTool(_ReadTool):
    descriptor = AgentToolDescriptor(
        name="device.flash",
        description="烧录开发板",
        input_schema=_ReadInput.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )


class _LargeReadTool(_ReadTool):
    descriptor = AgentToolDescriptor(
        name="workspace.read_large",
        description="读取大型工具结果",
        input_schema=_ReadInput.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del context
        self.calls += 1
        assert isinstance(arguments, _ReadInput)
        return ToolResult(
            success=True,
            output={"path": arguments.path, "content": "x" * 20_000},
            evidence_ids=["source:large.txt"],
        )


class _Stepper:
    def __init__(self, steps: list[AgentStep]) -> None:
        self.steps = list(steps)
        self.contexts: list[AgentStepContext] = []

    def decide_next_step(self, context: AgentStepContext) -> AgentStep:
        self.contexts.append(context)
        return self.steps.pop(0)


class _BlockingReplyStreamer(_Stepper):
    def __init__(self, step: AgentStep) -> None:
        super().__init__([step])
        self.first_chunk = threading.Event()
        self.release = threading.Event()

    def stream_reply(
        self,
        *,
        draft: str,
        context: AgentStepContext,
    ) -> object:
        del draft, context
        yield "第一段"
        self.first_chunk.set()
        assert self.release.wait(timeout=5)
        yield "第二段"


class _BlockingCommentaryStepper:
    def __init__(self) -> None:
        self.commentary_emitted = threading.Event()
        self.release = threading.Event()

    def decide_next_step_streaming(
        self,
        context: AgentStepContext,
        *,
        on_commentary: object,
    ) -> AgentStep:
        del context
        assert callable(on_commentary)
        on_commentary("我先检查显示驱动的寻址和偏移设置，")
        self.commentary_emitted.set()
        assert self.release.wait(timeout=5)
        on_commentary("再结合屏幕现象确认根因。")
        return AssistantReply(content="检查完成。")


def _initial_state() -> dict[str, object]:
    return {
        "session_id": "session-1",
        "turn_id": "turn-1",
        "project_key": "0:test4",
        "session_status": "active",
        "turn_status": "running",
        "objective_status": "none",
        "events": [
            ConversationEvent(
                event_id="turn-1:user",
                turn_id="turn-1",
                kind="user_message",
                sequence=1,
                payload={"content": "main.c 现在做什么"},
            )
        ],
        "step_count": 0,
        "max_steps": 10,
    }


def test_graph_runs_read_tool_then_returns_model_reply() -> None:
    tool = _ReadTool()
    stepper = _Stepper(
        [
            ToolCallBatch(
                calls=[
                    ToolCall(
                        call_id="read-main",
                        tool_name="workspace.read",
                        arguments={"path": "main/main.c"},
                    )
                ]
            ),
            AssistantReply(content="main.c 当前只有一个空的 app_main。"),
        ]
    )
    result = build_continuous_agent_graph().invoke(
        _initial_state(),
        context=ContinuousAgentRuntimeContext(
            stepper=stepper,
            tools=ToolRegistry([tool]),
        ),
    )

    assert result["turn_status"] == "completed"
    assert tool.calls == 1
    assert stepper.contexts[1].latest_tool_results[0]["status"] == "succeeded"
    assert [item.kind for item in result["events"]][-3:] == [
        "tool_call",
        "tool_result",
        "assistant_message",
    ]


def test_graph_prunes_large_tool_results_only_in_model_context() -> None:
    tool = _LargeReadTool()
    stepper = _Stepper(
        [
            ToolCallBatch(
                calls=[
                    ToolCall(
                        call_id="read-large",
                        tool_name="workspace.read_large",
                        arguments={"path": "large.txt"},
                    )
                ]
            ),
            AssistantReply(content="大型结果已检查。"),
        ]
    )

    result = build_continuous_agent_graph().invoke(
        _initial_state(),
        context=ContinuousAgentRuntimeContext(
            stepper=stepper,
            tools=ToolRegistry([tool]),
        ),
    )

    model_result = stepper.contexts[1].latest_tool_results[0]["result"]
    assert model_result["_luxar_truncated"] is True
    assert model_result["original_characters"] > 8_192
    recent_tool_result = next(
        item for item in stepper.contexts[1].recent_events if item.kind == "tool_result"
    )
    assert recent_tool_result.payload["result"]["_luxar_truncated"] is True
    # The checkpoint/tool ledger remains the source of truth and retains full evidence.
    assert len(result["tool_calls"]["step1:read-large"].result["content"]) == 20_000


def test_graph_forwards_reply_tokens_before_full_reply_is_ready() -> None:
    streamer = _BlockingReplyStreamer(AssistantReply(content="模型草稿"))
    reported: list[tuple[str, dict[str, object]]] = []
    results: list[dict[str, object]] = []

    def invoke() -> None:
        results.append(
            build_continuous_agent_graph().invoke(
                _initial_state(),
                context=ContinuousAgentRuntimeContext(
                    stepper=streamer,
                    reply_streamer=streamer,
                    tools=ToolRegistry(),
                    event_reporter=lambda event, data: reported.append((event, data)),
                ),
            )
        )

    worker = threading.Thread(target=invoke)
    worker.start()
    assert streamer.first_chunk.wait(timeout=5)
    assert ("token", {"token": "第一段"}) in reported
    phases = [data["phase"] for event, data in reported if event == "phase_changed"]
    assert phases[:2] == ["agent_decision", "agent_decision_completed"]
    assert not results

    streamer.release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert [data["token"] for event, data in reported if event == "token"] == [
        "第一段",
        "第二段",
    ]
    assistant = [
        item for item in results[0]["events"] if item.kind == "assistant_message"
    ][-1]
    assert assistant.payload["content"] == "第一段第二段"


def test_graph_forwards_model_commentary_before_decision_is_ready() -> None:
    stepper = _BlockingCommentaryStepper()
    reported: list[tuple[str, dict[str, object]]] = []
    results: list[dict[str, object]] = []

    worker = threading.Thread(
        target=lambda: results.append(
            build_continuous_agent_graph().invoke(
                _initial_state(),
                context=ContinuousAgentRuntimeContext(
                    stepper=stepper,  # type: ignore[arg-type]
                    tools=ToolRegistry(),
                    event_reporter=lambda event, data: reported.append((event, data)),
                ),
            )
        )
    )
    worker.start()

    assert stepper.commentary_emitted.wait(timeout=5)
    assert not results
    assert [data["token"] for event, data in reported if event == "commentary"] == [
        "我先检查显示驱动的寻址和偏移设置，"
    ]

    stepper.release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert "".join(
        str(data["token"])
        for event, data in reported
        if event == "commentary"
    ) == "我先检查显示驱动的寻址和偏移设置，再结合屏幕现象确认根因。"
    commentary_event = next(
        item for item in results[0]["events"] if item.kind == "assistant_commentary"
    )
    assert commentary_event.payload["phase"] == "commentary"


def test_graph_replays_commentary_into_the_next_model_step() -> None:
    class CommentaryToolStepper:
        def __init__(self) -> None:
            self.contexts: list[AgentStepContext] = []

        def decide_next_step_streaming(
            self,
            context: AgentStepContext,
            *,
            on_commentary: object,
        ) -> AgentStep:
            assert callable(on_commentary)
            self.contexts.append(context)
            if not context.latest_tool_results:
                on_commentary("我先读取 main.c，确认当前显示逻辑。")
                return ToolCallBatch(
                    calls=[
                        ToolCall(
                            call_id="read-after-commentary",
                            tool_name="workspace.read",
                            arguments={"path": "main/main.c"},
                        )
                    ]
                )
            return AssistantReply(content="已经确认当前显示逻辑。")

    stepper = CommentaryToolStepper()
    result = build_continuous_agent_graph().invoke(
        _initial_state(),
        context=ContinuousAgentRuntimeContext(
            stepper=stepper,  # type: ignore[arg-type]
            tools=ToolRegistry([_ReadTool()]),
        ),
    )

    assert result["turn_status"] == "completed"
    replayed = [
        event
        for event in stepper.contexts[1].recent_events
        if event.kind == "assistant_commentary"
    ]
    assert replayed[0].payload == {
        "content": "我先读取 main.c，确认当前显示逻辑。",
        "phase": "commentary",
    }


def test_finish_objective_uses_the_same_streaming_reply_path() -> None:
    streamer = _BlockingReplyStreamer(
        FinishObjective(outcome="completed", summary="内部完成摘要")
    )
    reported: list[tuple[str, dict[str, object]]] = []
    results: list[dict[str, object]] = []

    worker = threading.Thread(
        target=lambda: results.append(
            build_continuous_agent_graph().invoke(
                _initial_state(),
                context=ContinuousAgentRuntimeContext(
                    stepper=streamer,
                    reply_streamer=streamer,
                    tools=ToolRegistry(),
                    event_reporter=lambda event, data: reported.append((event, data)),
                ),
            )
        )
    )
    worker.start()
    assert streamer.first_chunk.wait(timeout=5)
    streamer.release.set()
    worker.join(timeout=5)

    assistant = [
        item for item in results[0]["events"] if item.kind == "assistant_message"
    ][-1]
    assert assistant.payload["content"] == "第一段第二段"


def test_graph_waits_only_for_typed_missing_input() -> None:
    stepper = _Stepper(
        [
            AskUser(
                request=MissingInputRequest(
                    request_id="select-board",
                    prompt="发现两块开发板，请选择一块",
                    fields=["board_id"],
                    reason="无法安全自动选择",
                )
            )
        ]
    )

    result = build_continuous_agent_graph().invoke(
        _initial_state(),
        context=ContinuousAgentRuntimeContext(
            stepper=stepper,
            tools=ToolRegistry(),
        ),
    )

    assert result["turn_status"] == "waiting_input"
    assert result["pending_request"].kind == "missing_input"


class _FailingStepper:
    def decide_next_step(self, context: AgentStepContext) -> AgentStep:
        del context
        raise CapabilityError(
            category="invalid_schema",
            message="bad model payload",
            retryable=False,
            details={"repair_validation_errors": [{"loc": ["step", "type"]}]},
        )


def test_graph_classifies_model_failure_without_asking_user() -> None:
    result = build_continuous_agent_graph().invoke(
        _initial_state(),
        context=ContinuousAgentRuntimeContext(
            stepper=_FailingStepper(),
            tools=ToolRegistry(),
        ),
    )

    assert result["turn_status"] == "failed"
    assert result["last_failure"].category == "model"
    assert result["last_failure"].message == (
        "模型返回的下一步字段不符合 Agent 决策协议"
    )
    assert result["last_failure"].details["adapter_message"] == "bad model payload"
    assert result["last_failure"].details["repair_validation_errors"]
    assert result.get("pending_request") is None


class _ProjectChangeWorkflow:
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
        assert context.session_id == "session-1"
        return DomainWorkflowOutcome(
            status="waiting_approval",
            summary="准备修改 main/main.c",
            result={"planned": True},
            pending_approval=ToolApprovalRequest(
                request_id="domain:change-1:task-1",
                call_id=call.call_id,
                tool_name="project.change",
                summary="准备修改 main/main.c",
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
        assert feedback == "允许修改"
        return DomainWorkflowOutcome(
            status="completed",
            summary="复杂工程变更与验收已完成",
            result={"changed_files": ["main/main.c"]},
        )


def test_graph_delegates_complex_change_and_returns_result_to_top_agent() -> None:
    workflow = _ProjectChangeWorkflow()
    stepper = _Stepper(
        [
            DomainWorkflowCall(
                call_id="change-1",
                workflow_name="project.change",
                task="修改 OLED 初始化并保持 TWAI 能力",
            ),
            AssistantReply(content="修改和非回归验收均已完成。"),
        ]
    )
    context = ContinuousAgentRuntimeContext(
        stepper=stepper,
        tools=ToolRegistry(),
        domain_workflows=DomainWorkflowRegistry([workflow]),
    )
    graph = build_continuous_agent_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "domain-session"}}

    interrupted = graph.invoke(_initial_state(), config=config, context=context)

    assert interrupted["turn_status"] == "waiting_approval"
    assert workflow.starts == 1
    assert workflow.resumes == 0
    assert interrupted["__interrupt__"]

    resumed = graph.invoke(
        Command(resume={"approved": True, "feedback": "允许修改"}),
        config=config,
        context=context,
    )

    assert resumed["turn_status"] == "completed"
    assert workflow.resumes == 1
    assert resumed["domain_calls"]["change-1"]["status"] == "completed"
    assert resumed["objective_status"] == "completed"
    assert resumed["active_objective"].status == "completed"
    assert stepper.contexts[-1].latest_domain_results[0]["result"] == {
        "changed_files": ["main/main.c"]
    }


def test_finish_objective_cancels_goal_without_archiving_session() -> None:
    initial = _initial_state()
    initial["active_objective"] = {
        "objective_id": "objective-1",
        "title": "烧录并验证",
        "description": "构建、烧录并读取串口",
        "status": "active",
        "priority": 50,
        "acceptance_criteria": [],
        "constraints": [],
        "source_message_ids": ["turn-1"],
        "revision": 1,
    }
    result = build_continuous_agent_graph().invoke(
        initial,
        context=ContinuousAgentRuntimeContext(
            stepper=_Stepper(
                [
                    FinishObjective(
                        outcome="abandoned",
                        summary="已停止当前目标，会话仍可继续。",
                    )
                ]
            ),
            tools=ToolRegistry(),
        ),
    )

    assert result["objective_status"] == "abandoned"
    assert result["active_objective"].status == "cancelled"
    assert result["active_objective"].revision == 2
    assert result["session_status"] == "active"


class _Compactor:
    def __init__(self) -> None:
        self.compacted_event_count = 0

    def compact_context(
        self,
        *,
        previous_summary: str,
        events: list[ConversationEvent],
    ) -> str:
        assert previous_summary == "旧摘要"
        self.compacted_event_count = len(events)
        return "保留的目标、约束、工具证据和未完成事项"


def test_graph_compacts_early_events_before_model_decision() -> None:
    compactor = _Compactor()
    stepper = _Stepper([AssistantReply(content="已读取压缩后的上下文。")])
    initial = _initial_state()
    initial["context_summary"] = "旧摘要"
    initial["events"] = [
        ConversationEvent(
            event_id=f"turn-{index}:message",
            turn_id=f"turn-{index}",
            kind="user_message" if index % 2 else "assistant_message",
            sequence=index,
            payload={"content": f"事件 {index}"},
        )
        for index in range(1, 86)
    ]

    result = build_continuous_agent_graph().invoke(
        initial,
        context=ContinuousAgentRuntimeContext(
            stepper=stepper,
            tools=ToolRegistry(),
            context_compactor=compactor,
        ),
    )

    assert compactor.compacted_event_count == 45
    assert result["context_summary"] == "保留的目标、约束、工具证据和未完成事项"
    assert result["compaction_cursor"] == 45
    assert len(stepper.contexts[0].recent_events) == 40


def test_graph_interrupts_before_approval_tool_and_resumes_once() -> None:
    tool = _ApprovalTool()
    stepper = _Stepper(
        [
            ToolCallBatch(
                calls=[
                    ToolCall(
                        call_id="flash-device",
                        tool_name="device.flash",
                        arguments={"path": "build/firmware.bin"},
                    )
                ]
            ),
            AssistantReply(content="烧录完成。"),
        ]
    )
    context = ContinuousAgentRuntimeContext(
        stepper=stepper,
        tools=ToolRegistry([tool]),
    )
    graph = build_continuous_agent_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "approval-session"}}

    interrupted = graph.invoke(_initial_state(), config=config, context=context)

    assert tool.calls == 0
    assert interrupted["turn_status"] == "waiting_approval"
    assert interrupted["__interrupt__"]

    resumed = graph.invoke(
        Command(resume={"approved": True, "feedback": "同意烧录"}),
        config=config,
        context=context,
    )

    assert tool.calls == 1
    assert resumed["turn_status"] == "completed"
    assert resumed["tool_calls"]["step1:flash-device"].status == "succeeded"
    assert any(item.kind == "approval_decision" for item in resumed["events"])


def test_graph_ingests_runtime_steering_at_next_safe_boundary() -> None:
    tool = _ReadTool()
    stepper = _Stepper(
        [
            ToolCallBatch(
                calls=[
                    ToolCall(
                        call_id="read-before-steering",
                        tool_name="workspace.read",
                        arguments={"path": "main/main.c"},
                    )
                ]
            ),
            AssistantReply(content="已按运行中追加的 COM4 指令继续。"),
        ]
    )
    drains = iter(
        [
            [],
            [SteeringMessage(steering_id="steer-1", message="改用 COM4")],
        ]
    )

    result = build_continuous_agent_graph().invoke(
        _initial_state(),
        context=ContinuousAgentRuntimeContext(
            stepper=stepper,
            tools=ToolRegistry([tool]),
            drain_steering=lambda: next(drains, []),
        ),
    )

    assert result["turn_status"] == "completed"
    assert tool.calls == 1
    steering = [
        event
        for event in stepper.contexts[-1].recent_events
        if event.payload.get("steering") is True
    ]
    assert [event.payload["content"] for event in steering] == ["改用 COM4"]


def test_graph_cancels_before_tool_side_effect_at_safe_boundary() -> None:
    tool = _ReadTool()
    stepper = _Stepper(
        [
            ToolCallBatch(
                calls=[
                    ToolCall(
                        call_id="must-not-run",
                        tool_name="workspace.read",
                        arguments={"path": "main/main.c"},
                    )
                ]
            )
        ]
    )
    cancellation_checks = iter([False, True])

    result = build_continuous_agent_graph().invoke(
        _initial_state(),
        context=ContinuousAgentRuntimeContext(
            stepper=stepper,
            tools=ToolRegistry([tool]),
            cancellation_requested=lambda: next(cancellation_checks, True),
        ),
    )

    assert result["turn_status"] == "cancelled"
    assert result["session_status"] == "active"
    assert tool.calls == 0
    assert result["events"][-1].kind == "assistant_message"


def test_assistant_message_never_replays_previous_turn_content() -> None:
    """当前 turn 未生成回复时，不得回退复读历史 turn 的 assistant 内容。"""

    from luxar.application.continuous_agent_runner import ContinuousAgentRunResult
    from luxar.web_continuous_agent import _assistant_message

    result = ContinuousAgentRunResult(
        thread_id="session",
        state={
            "turn_id": "turn-b",
            "turn_status": "completed",
            "objective_status": "active",
            "events": [
                ConversationEvent(
                    event_id="turn-a:assistant",
                    turn_id="turn-a",
                    kind="assistant_message",
                    sequence=1,
                    payload={"content": "上一轮的旧回复（ESP_FAIL 分析）"},
                ),
                ConversationEvent(
                    event_id="turn-b:user",
                    turn_id="turn-b",
                    kind="user_message",
                    sequence=2,
                    payload={"content": "我看到字体显示了"},
                ),
                ConversationEvent(
                    event_id="turn-b:tool_result",
                    turn_id="turn-b",
                    kind="tool_result",
                    sequence=3,
                    payload={"status": "blocked"},
                ),
            ],
        },
    )

    message = _assistant_message(result)

    assert "ESP_FAIL" not in message
    assert message == "本轮没有产生可展示的回复。"


def test_assistant_message_reports_rejected_approval_with_user_feedback() -> None:
    """审批被拒且当前 turn 无回复时，输出含用户反馈的语义化说明。"""

    from luxar.application.continuous_agent_runner import ContinuousAgentRunResult
    from luxar.web_continuous_agent import _assistant_message

    result = ContinuousAgentRunResult(
        thread_id="session",
        state={
            "turn_id": "turn-b",
            "turn_status": "running",
            "objective_status": "blocked",
            "domain_approvals": {"wf_4": False},
            "domain_approval_feedback": {"wf_4": "初始化已经成功了，问题是右侧两列杂像素"},
            "events": [
                ConversationEvent(
                    event_id="turn-b:user",
                    turn_id="turn-b",
                    kind="user_message",
                    sequence=0,
                    payload={"content": "右侧两列杂像素"},
                ),
                ConversationEvent(
                    event_id="turn-b:domain-result",
                    turn_id="turn-b",
                    kind="tool_result",
                    sequence=1,
                    payload={"status": "blocked"},
                ),
            ],
        },
    )

    message = _assistant_message(result)

    assert "拒绝" in message
    assert "右侧两列杂像素" in message


def test_assistant_message_uses_current_turn_content_when_present() -> None:
    """当前 turn 有回复时取当前回复，不受历史影响。"""

    from luxar.application.continuous_agent_runner import ContinuousAgentRunResult
    from luxar.web_continuous_agent import _assistant_message

    result = ContinuousAgentRunResult(
        thread_id="session",
        state={
            "turn_id": "turn-b",
            "events": [
                ConversationEvent(
                    event_id="turn-a:assistant",
                    turn_id="turn-a",
                    kind="assistant_message",
                    sequence=1,
                    payload={"content": "旧回复"},
                ),
                ConversationEvent(
                    event_id="turn-b:assistant",
                    turn_id="turn-b",
                    kind="assistant_message",
                    sequence=2,
                    payload={"content": "当前回复"},
                ),
            ],
        },
    )

    assert _assistant_message(result) == "当前回复"


class _FontLikeTool:
    """模拟 font.extract：按 path 返回对应字形，记录每次被调用的参数。"""

    input_model = _ReadInput
    descriptor = AgentToolDescriptor(
        name="font.extract",
        description="生成字模",
        input_schema=_ReadInput.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del context
        assert isinstance(arguments, _ReadInput)
        self.calls.append(arguments.path)
        return ToolResult(
            success=True,
            output={"path": arguments.path, "glyph": arguments.path[:1]},
            evidence_ids=[f"font:{arguments.path}"],
        )


class _CollidingCallIdStepper:
    """第一步用 call_id 1/2，第二步又用 call_id 1..3（重现 oled4 撞号场景）。"""

    def __init__(self) -> None:
        self.decisions = 0

    def decide_next_step(self, context: AgentStepContext) -> AgentStep:
        del context
        self.decisions += 1
        if self.decisions == 1:
            return ToolCallBatch(
                calls=[
                    ToolCall(
                        call_id="1",
                        tool_name="font.extract",
                        arguments={"path": "driver.search"},
                    ),
                    ToolCall(
                        call_id="2",
                        tool_name="font.extract",
                        arguments={"path": "knowledge.search"},
                    ),
                ]
            )
        if self.decisions == 2:
            return ToolCallBatch(
                calls=[
                    ToolCall(
                        call_id="1",
                        tool_name="font.extract",
                        arguments={"path": "A"},
                    ),
                    ToolCall(
                        call_id="2",
                        tool_name="font.extract",
                        arguments={"path": "B"},
                    ),
                    ToolCall(
                        call_id="3",
                        tool_name="font.extract",
                        arguments={"path": "C"},
                    ),
                ]
            )
        return AssistantReply(content="字模生成完成。")


def test_tool_call_ids_reused_across_steps_do_not_collide() -> None:
    """回归：同 turn 两步复用 call_id 不再撞号崩溃（修复 oled4 持续 Agent 失败）。"""
    tool = _FontLikeTool()
    result = build_continuous_agent_graph().invoke(
        _initial_state(),
        context=ContinuousAgentRuntimeContext(
            stepper=_CollidingCallIdStepper(),  # type: ignore[arg-type]
            tools=ToolRegistry([tool]),
        ),
    )

    assert result["turn_status"] == "completed"
    assert result.get("last_failure") is None
    assert tool.calls == [
        "driver.search",
        "knowledge.search",
        "A",
        "B",
        "C",
    ]
    # 重映射后状态键 turn 内唯一：step1:1/step1:2/step2:1/step2:2/step2:3
    state_keys = set(result["tool_calls"])
    assert state_keys == {
        "step1:1",
        "step1:2",
        "step2:1",
        "step2:2",
        "step2:3",
    }
    # 事件 ID 也按重映射后的 call_id 生成，不与第一步冲突
    event_ids = [
        event.event_id for event in result["events"] if event.kind == "tool_call"
    ]
    assert "turn-1:tool-call:step2:1" in event_ids
    assert len(event_ids) == 5
