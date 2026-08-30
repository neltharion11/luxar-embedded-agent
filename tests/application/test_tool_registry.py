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


class _ReverseTool:
    input_model = _EchoInput
    descriptor = AgentToolDescriptor(
        name="workspace.read",
        description="返回反转文本",
        input_schema=_EchoInput.model_json_schema(),
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
        del context
        self.calls += 1
        assert isinstance(arguments, _EchoInput)
        return ToolResult(
            success=True,
            output={"text": arguments.text[::-1]},
            evidence_ids=[f"rev:{arguments.text}"],
        )


def test_registry_derives_new_key_when_call_id_reused_with_different_tool() -> None:
    """同一 call_id 被不同工具复用：不再 raise，派生新键且结果互不串扰。"""
    echo = _EchoTool()
    reverse = _ReverseTool()
    registry = ToolRegistry([echo, reverse])
    first = ToolCall(
        call_id="call-1",
        tool_name="workspace.echo",
        arguments={"text": "AB"},
    )
    second = ToolCall(
        call_id="call-1",
        tool_name="workspace.read",
        arguments={"text": "AB"},
    )

    outcome1 = registry.dispatch(first, _context())
    outcome2 = registry.dispatch(second, _context())

    assert outcome1.call.status == "succeeded"
    assert outcome2.call.status == "succeeded"
    assert outcome1.call.idempotency_key != outcome2.call.idempotency_key
    assert outcome2.call.idempotency_key.endswith("#1")
    assert outcome1.call.result == {"text": "AB", "project_key": "0:test4"}
    assert outcome2.call.result == {"text": "BA"}
    assert echo.calls == 1
    assert reverse.calls == 1
    # 各自精确回放
    assert registry.dispatch(first, _context()) == outcome1
    assert registry.dispatch(second, _context()) == outcome2
    assert echo.calls == 1
    assert reverse.calls == 1


def test_registry_derives_new_key_when_call_id_reused_with_different_arguments() -> None:
    """同一 call_id 同一工具但参数不同：两次都执行，各自独立幂等。"""
    tool = _EchoTool()
    registry = ToolRegistry([tool])
    first = ToolCall(
        call_id="call-1",
        tool_name="workspace.echo",
        arguments={"text": "A"},
    )
    second = ToolCall(
        call_id="call-1",
        tool_name="workspace.echo",
        arguments={"text": "B"},
    )

    outcome1 = registry.dispatch(first, _context())
    outcome2 = registry.dispatch(second, _context())

    assert outcome1.call.status == "succeeded"
    assert outcome2.call.status == "succeeded"
    assert outcome1.call.idempotency_key != outcome2.call.idempotency_key
    assert outcome1.call.result == {"text": "A", "project_key": "0:test4"}
    assert outcome2.call.result == {"text": "B", "project_key": "0:test4"}
    assert tool.calls == 2
    # 精确回放仍幂等
    assert registry.dispatch(first, _context()) == outcome1
    assert registry.dispatch(second, _context()) == outcome2
    assert tool.calls == 2


def test_ledger_collision_keeps_both_records_and_replays_after_restart() -> None:
    """撞号派生键必须对 ledger 与内存缓存同时生效，重启后也按各自结果回放。"""
    ledger = TransientPersistence()
    ledger.create_agent_session(
        session_id="session-1",
        project_key="0:test4",
    )
    ledger.start_agent_turn(
        turn_id="turn-1",
        session_id="session-1",
        client_turn_id="client-1",
        user_message="执行",
    )
    echo = _EchoTool()
    reverse = _ReverseTool()
    first = ToolCall(
        call_id="call-1",
        tool_name="workspace.echo",
        arguments={"text": "AB"},
    )
    second = ToolCall(
        call_id="call-1",
        tool_name="workspace.read",
        arguments={"text": "AB"},
    )

    registry = ToolRegistry([echo, reverse], ledger=ledger)
    outcome1 = registry.dispatch(first, _context())
    outcome2 = registry.dispatch(second, _context())

    assert outcome1.call.status == "succeeded"
    assert outcome2.call.status == "succeeded"
    key1 = outcome1.call.idempotency_key
    key2 = outcome2.call.idempotency_key
    assert key1 != key2
    # ledger 中两条记录按各自工具落库，互不串扰
    assert ledger.get_tool_execution(key1).tool_name == "workspace.echo"
    assert ledger.get_tool_execution(key2).tool_name == "workspace.read"

    restarted_echo = _EchoTool()
    restarted_reverse = _ReverseTool()
    fresh = ToolRegistry([restarted_echo, restarted_reverse], ledger=ledger)
    replay1 = fresh.dispatch(first, _context())
    replay2 = fresh.dispatch(second, _context())

    assert replay1.call.result == outcome1.call.result
    assert replay2.call.result == outcome2.call.result
    # 关键：重启后第二条不能把 echo 的结果伪装成 read 的结果
    assert replay2.call.result == {"text": "BA"}
    assert restarted_echo.calls == 0
    assert restarted_reverse.calls == 0


def test_ledger_accepts_large_arguments_fingerprint() -> None:
    """回归：apply_change_bundle 等大参数（指纹 >8KB，含完整文件内容）在
    ledger 预留/回读/重启回放时不得触发 ToolExecutionRecord 校验失败
    （历史上 8_000 上限导致审批恢复崩溃成 approval_resume_failed）。"""
    ledger = TransientPersistence()
    ledger.create_agent_session(
        session_id="session-1",
        project_key="0:test4",
    )
    ledger.start_agent_turn(
        turn_id="turn-1",
        session_id="session-1",
        client_turn_id="client-1",
        user_message="写入驱动",
    )
    tool = _EchoTool()
    big_text = "x" * 20_000
    call = ToolCall(
        call_id="call-big",
        tool_name="workspace.echo",
        arguments={"text": big_text},
    )

    outcome = ToolRegistry([tool], ledger=ledger).dispatch(call, _context())

    assert outcome.call.status == "succeeded"
    assert outcome.call.result == {"text": big_text, "project_key": "0:test4"}
    assert tool.calls == 1
    record = ledger.get_tool_execution(outcome.call.idempotency_key)
    assert record is not None
    assert len(record.arguments_fingerprint) > 8_000
    # 重启后仍可精确回放
    replay = ToolRegistry([_EchoTool()], ledger=ledger).dispatch(call, _context())
    assert replay.call.status == "succeeded"
    assert replay.call.result == outcome.call.result
