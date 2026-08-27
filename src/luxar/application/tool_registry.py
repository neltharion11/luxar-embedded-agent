"""Typed tool registry, policy gate, and process-local idempotency cache."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from luxar.domain.continuous_agent.failures import ContinuousAgentFailure
from luxar.domain.continuous_agent.requests import ToolApprovalRequest
from luxar.domain.continuous_agent.steps import AgentToolDescriptor, ToolCall
from luxar.domain.continuous_agent.tools import ToolCallState, ToolResult
from luxar.domain.continuous_agent.tools import ToolExecutionRecord
from luxar.ports.agent_tool import (
    AgentToolExecutionContext,
    AgentToolPort,
    ToolExecutionLedgerPort,
)


@dataclass(frozen=True)
class ToolDispatchOutcome:
    call: ToolCallState
    pending_approval: ToolApprovalRequest | None = None


class ToolRegistry:
    """Own registered tools; model output never bypasses this boundary."""

    def __init__(
        self,
        tools: list[AgentToolPort] | None = None,
        *,
        ledger: ToolExecutionLedgerPort | None = None,
    ) -> None:
        self._tools: dict[str, AgentToolPort] = {}
        self._completed: dict[str, tuple[str, str, ToolDispatchOutcome]] = {}
        self._ledger = ledger
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: AgentToolPort) -> None:
        name = tool.descriptor.name
        if name in self._tools:
            raise ValueError(f"Agent tool already registered: {name}")
        expected_schema = tool.input_model.model_json_schema()
        if tool.descriptor.input_schema != expected_schema:
            raise ValueError(f"Agent tool schema mismatch: {name}")
        self._tools[name] = tool

    def descriptors(self) -> list[AgentToolDescriptor]:
        return [self._tools[name].descriptor for name in sorted(self._tools)]

    @staticmethod
    def _idempotency_key(call: ToolCall, context: AgentToolExecutionContext) -> str:
        return f"{context.session_id}:{context.turn_id}:{call.call_id}"

    @staticmethod
    def _arguments_fingerprint(arguments: dict[str, object]) -> str:
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _outcome_from_record(
        record: ToolExecutionRecord,
        call: ToolCall,
    ) -> ToolDispatchOutcome:
        if record.status == "running":
            return ToolDispatchOutcome(
                call=ToolCallState(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    arguments=call.arguments,
                    status="indeterminate",
                    idempotency_key=record.idempotency_key,
                    failure=ContinuousAgentFailure(
                        category="tool",
                        code="execution_already_running",
                        message="工具调用已有未确认的执行记录，禁止自动重复",
                        retryable=False,
                    ),
                )
            )
        stored_result = dict(record.result or {})
        stored_evidence = stored_result.pop("__luxar_evidence_ids", [])
        return ToolDispatchOutcome(
            call=ToolCallState(
                call_id=call.call_id,
                tool_name=call.tool_name,
                arguments=call.arguments,
                status=record.status,
                idempotency_key=record.idempotency_key,
                result=stored_result or None,
                evidence_ids=(
                    [str(item) for item in stored_evidence]
                    if isinstance(stored_evidence, list)
                    else []
                ),
                failure=record.failure,
            )
        )

    def _reserve_execution(
        self,
        call: ToolCall,
        context: AgentToolExecutionContext,
        *,
        idempotency_key: str,
        fingerprint: str,
    ) -> tuple[ToolExecutionRecord | None, bool]:
        if self._ledger is None:
            return None, True
        return self._ledger.reserve_tool_execution(
            idempotency_key=idempotency_key,
            session_id=context.session_id,
            turn_id=context.turn_id,
            call_id=call.call_id,
            tool_name=call.tool_name,
            arguments_fingerprint=fingerprint,
        )

    def dispatch(
        self,
        call: ToolCall,
        context: AgentToolExecutionContext,
        *,
        approved: bool | None = None,
    ) -> ToolDispatchOutcome:
        idempotency_key = self._idempotency_key(call, context)
        fingerprint = self._arguments_fingerprint(call.arguments)
        cached = self._completed.get(idempotency_key)
        if cached is not None:
            cached_name, cached_fingerprint, outcome = cached
            if cached_name != call.tool_name or cached_fingerprint != fingerprint:
                raise ValueError("Tool idempotency key was reused with new arguments")
            return outcome

        tool = self._tools.get(call.tool_name)
        if tool is None:
            return ToolDispatchOutcome(
                call=ToolCallState(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    arguments=call.arguments,
                    status="failed",
                    idempotency_key=idempotency_key,
                    failure=ContinuousAgentFailure(
                        category="policy",
                        code="unknown_tool",
                        message="模型请求了未注册工具",
                        retryable=False,
                    ),
                )
            )

        try:
            parsed_arguments = tool.input_model.model_validate(call.arguments)
        except ValidationError as error:
            return ToolDispatchOutcome(
                call=ToolCallState(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    arguments=call.arguments,
                    status="failed",
                    idempotency_key=idempotency_key,
                    failure=ContinuousAgentFailure(
                        category="validation",
                        code="invalid_tool_arguments",
                        message="工具参数不符合输入合同",
                        retryable=True,
                        details={
                            "errors": error.errors(include_url=False),
                        },
                    ),
                )
            )

        policy_validator = getattr(tool, "validate_policy", None)
        if callable(policy_validator):
            try:
                policy_failure = policy_validator(parsed_arguments, context)
            except Exception:
                policy_failure = ContinuousAgentFailure(
                    category="policy",
                    code="tool_policy_check_failed",
                    message="工具安全策略检查失败",
                    retryable=True,
                )
            if policy_failure is not None:
                return ToolDispatchOutcome(
                    call=ToolCallState(
                        call_id=call.call_id,
                        tool_name=call.tool_name,
                        arguments=call.arguments,
                        status="failed",
                        idempotency_key=idempotency_key,
                        failure=policy_failure,
                    )
                )

        if tool.descriptor.requires_approval and approved is None:
            target = call.arguments.get("serial_port")
            target_suffix = f"（目标：{target}）" if target else ""
            return ToolDispatchOutcome(
                call=ToolCallState(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    arguments=call.arguments,
                    status="waiting_approval",
                    idempotency_key=idempotency_key,
                ),
                pending_approval=ToolApprovalRequest(
                    request_id=f"approval:{call.call_id}",
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    summary=f"执行工具 {call.tool_name}{target_suffix}",
                    risk="device" if call.tool_name.startswith("device.") else "write",
                ),
            )
        if tool.descriptor.requires_approval and approved is False:
            record, created = self._reserve_execution(
                call,
                context,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if record is not None and not created:
                return self._outcome_from_record(record, call)
            failure = ContinuousAgentFailure(
                category="policy",
                code="approval_rejected",
                message="用户拒绝了工具执行",
                retryable=False,
            )
            outcome = ToolDispatchOutcome(
                call=ToolCallState(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    arguments=call.arguments,
                    status="rejected",
                    idempotency_key=idempotency_key,
                    failure=failure,
                )
            )
            if self._ledger is not None:
                self._ledger.finish_tool_execution(
                    idempotency_key,
                    status="rejected",
                    failure=failure,
                )
            self._completed[idempotency_key] = (
                call.tool_name,
                fingerprint,
                outcome,
            )
            return outcome

        record, created = self._reserve_execution(
            call,
            context,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
        )
        if record is not None and not created:
            return self._outcome_from_record(record, call)

        try:
            result = tool.execute(parsed_arguments, context)
        except Exception:
            result = ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code="tool_execution_failed",
                    message="工具执行失败，请检查工具日志",
                    retryable=True,
                ),
            )
        outcome = ToolDispatchOutcome(
            call=ToolCallState(
                call_id=call.call_id,
                tool_name=call.tool_name,
                arguments=call.arguments,
                status="succeeded" if result.success else "failed",
                idempotency_key=idempotency_key,
                result=result.output,
                evidence_ids=result.evidence_ids,
                failure=result.failure,
            )
        )
        if self._ledger is not None:
            ledger_result = dict(result.output)
            if result.evidence_ids:
                ledger_result["__luxar_evidence_ids"] = result.evidence_ids
            self._ledger.finish_tool_execution(
                idempotency_key,
                status="succeeded" if result.success else "failed",
                result=ledger_result,
                failure=result.failure,
            )
        self._completed[idempotency_key] = (
            call.tool_name,
            fingerprint,
            outcome,
        )
        return outcome


__all__ = ["ToolDispatchOutcome", "ToolRegistry"]
