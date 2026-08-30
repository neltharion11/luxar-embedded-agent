from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from luxar.application.continuous_agent_graph import ContinuousAgentRuntimeContext
from luxar.application.continuous_agent_runner import (
    resume_continuous_agent_workflow,
    run_continuous_agent_workflow,
)
from luxar.application.tool_registry import ToolRegistry
from luxar.domain.continuous_agent.events import ConversationEvent
from luxar.domain.continuous_agent.steps import (
    AgentStep,
    AgentStepContext,
    AgentToolDescriptor,
    AssistantReply,
    ToolCall,
    ToolCallBatch,
)
from luxar.domain.continuous_agent.tools import ToolResult
from luxar.ports.agent_tool import AgentToolExecutionContext


class _FlashInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    serial_port: str


class _FlashTool:
    input_model = _FlashInput
    descriptor = AgentToolDescriptor(
        name="device.flash",
        description="烧录固件",
        input_schema=_FlashInput.model_json_schema(),
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
        self.calls += 1
        assert isinstance(arguments, _FlashInput)
        return ToolResult(
            success=True,
            output={"serial_port": arguments.serial_port, "flashed": True},
        )


class _Stepper:
    def __init__(self, steps: list[AgentStep]) -> None:
        self.steps = list(steps)

    def decide_next_step(self, context: AgentStepContext) -> AgentStep:
        del context
        return self.steps.pop(0)


def _state(turn_id: str, content: str) -> dict[str, object]:
    return {
        "session_id": "session-runner",
        "turn_id": turn_id,
        "project_key": "0:test4",
        "events": [
            ConversationEvent(
                event_id=f"{turn_id}:user",
                turn_id=turn_id,
                kind="user_message",
                sequence=1,
                payload={"content": content},
            )
        ],
    }


def test_runner_resumes_same_checkpoint_after_tool_approval() -> None:
    tool = _FlashTool()
    context = ContinuousAgentRuntimeContext(
        stepper=_Stepper(
            [
                ToolCallBatch(
                    calls=[
                        ToolCall(
                            call_id="flash-1",
                            tool_name="device.flash",
                            arguments={"serial_port": "COM4"},
                        )
                    ]
                ),
                AssistantReply(content="COM4 烧录完成。"),
            ]
        ),
        tools=ToolRegistry([tool]),
    )

    interrupted = run_continuous_agent_workflow(
        initial_state=_state("turn-1", "烧录到 COM4"),  # type: ignore[arg-type]
        context=context,
        thread_id="session-runner",
    )

    assert interrupted.pending_approval is not None
    # call_id 已被 execute_tools 重映射为 turn 内全局唯一（step{step_count}:{raw}）
    assert interrupted.pending_approval.call_id == "step1:flash-1"
    assert tool.calls == 0

    resumed = resume_continuous_agent_workflow(
        thread_id="session-runner",
        context=context,
        checkpointer=interrupted.checkpointer,  # type: ignore[arg-type]
        approved=True,
        feedback="允许",
    )

    assert resumed.pending_approval is None
    assert resumed.state["turn_status"] == "completed"
    assert tool.calls == 1


def test_runner_keeps_prior_events_on_next_turn_in_same_session() -> None:
    context = ContinuousAgentRuntimeContext(
        stepper=_Stepper(
            [
                AssistantReply(content="第一轮完成。"),
                AssistantReply(content="我记得第一轮。"),
            ]
        ),
        tools=ToolRegistry(),
    )
    first = run_continuous_agent_workflow(
        initial_state=_state("turn-1", "第一轮"),  # type: ignore[arg-type]
        context=context,
        thread_id="session-runner",
    )
    second = run_continuous_agent_workflow(
        initial_state=_state("turn-2", "你记得吗"),  # type: ignore[arg-type]
        context=context,
        thread_id="session-runner",
        checkpointer=first.checkpointer,
    )

    assert [event.event_id for event in second.state["events"]] == [
        "turn-1:user",
        "turn-1:assistant",
        "turn-2:user",
        "turn-2:assistant",
    ]
