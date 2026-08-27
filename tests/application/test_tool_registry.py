from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from luxar.application.tool_registry import ToolRegistry
from luxar.database import TransientPersistence
from luxar.domain.continuous_agent.steps import AgentToolDescriptor, ToolCall
from luxar.domain.continuous_agent.tools import ToolResult
from luxar.ports.agent_tool import AgentToolExecutionContext


class _EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str


class _EchoTool:
    input_model = _EchoInput

    def __init__(self, *, approval: bool = False) -> None:
        self.calls = 0
        self.descriptor = AgentToolDescriptor(
            name="device.echo" if approval else "workspace.echo",
            description="返回输入内容",
            input_schema=self.input_model.model_json_schema(),
            read_only=not approval,
            requires_approval=approval,
        )

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        self.calls += 1
        assert isinstance(arguments, _EchoInput)
        return ToolResult(
            success=True,
            output={
                "text": arguments.text,
                "project_key": context.project_key,
            },
            evidence_ids=[f"echo:{arguments.text}"],
        )


def _context() -> AgentToolExecutionContext:
    return AgentToolExecutionContext(
        session_id="session-1",
        turn_id="turn-1",
        project_key="0:test4",
    )


def test_registry_validates_and_idempotently_executes_read_tool() -> None:
    tool = _EchoTool()
    registry = ToolRegistry([tool])
    call = ToolCall(
        call_id="call-1",
        tool_name="workspace.echo",
        arguments={"text": "COM4"},
    )

    first = registry.dispatch(call, _context())
    replay = registry.dispatch(call, _context())

    assert first.call.status == "succeeded"
    assert first.call.result == {"text": "COM4", "project_key": "0:test4"}
    assert replay == first
    assert replay.call.evidence_ids == ["echo:COM4"]
    assert tool.calls == 1


def test_registry_rejects_invalid_arguments_before_tool_execution() -> None:
    tool = _EchoTool()
    outcome = ToolRegistry([tool]).dispatch(
        ToolCall(
            call_id="call-invalid",
            tool_name="workspace.echo",
            arguments={"unexpected": True},
        ),
        _context(),
    )

    assert outcome.call.status == "failed"
    assert outcome.call.failure is not None
    assert outcome.call.failure.category == "validation"
    assert tool.calls == 0


def test_registry_pauses_approval_tool_before_side_effect() -> None:
    tool = _EchoTool(approval=True)
    registry = ToolRegistry([tool])
    call = ToolCall(
        call_id="call-device",
        tool_name="device.echo",
        arguments={"text": "flash"},
    )

    pending = registry.dispatch(call, _context())
    assert tool.calls == 0
    completed = registry.dispatch(call, _context(), approved=True)

    assert pending.call.status == "waiting_approval"
    assert pending.pending_approval is not None
    assert completed.call.status == "succeeded"
    assert tool.calls == 1


def test_registry_unknown_tool_is_policy_failure() -> None:
    outcome = ToolRegistry().dispatch(
        ToolCall(
            call_id="call-unknown",
            tool_name="system.shell",
            arguments={},
        ),
        _context(),
    )

    assert outcome.call.failure is not None
    assert outcome.call.failure.category == "policy"
    assert outcome.call.failure.code == "unknown_tool"


def test_persistent_ledger_prevents_execution_after_registry_restart() -> None:
    ledger = TransientPersistence()
    ledger.create_agent_session(
        session_id="session-1",
        project_key="0:test4",
    )
    ledger.start_agent_turn(
        turn_id="turn-1",
        session_id="session-1",
        client_turn_id="client-1",
        user_message="读取",
    )
    first_tool = _EchoTool()
    call = ToolCall(
        call_id="call-persistent",
        tool_name="workspace.echo",
        arguments={"text": "once"},
    )
    first = ToolRegistry([first_tool], ledger=ledger).dispatch(call, _context())

    restarted_tool = _EchoTool()
    replay = ToolRegistry([restarted_tool], ledger=ledger).dispatch(
        call,
        _context(),
    )

    assert first.call.status == "succeeded"
    assert replay.call.status == "succeeded"
    assert replay.call.result == first.call.result
    assert replay.call.evidence_ids == ["echo:once"]
    assert first_tool.calls == 1
    assert restarted_tool.calls == 0
