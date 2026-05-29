from __future__ import annotations

import asyncio


MAX_AGENT_TOOL_CALLS = 50
MAX_AGENT_TOOL_TIMEOUT_SEC = 180
TOOL_TIMEOUT_OVERRIDES: dict[str, int] = {
    "workspace_build": 300,
    "workspace_flash": 300,
    "analyze_document_engineering": 300,
}


class AgentToolLimitError(RuntimeError):
    """Raised when the agent exceeds the allowed number of tool calls."""


class AgentToolTimeoutError(RuntimeError):
    """Raised when a single tool call exceeds the allowed runtime."""


def enforce_tool_call_budget(tool_name: str, used_calls: int, *, max_calls: int = MAX_AGENT_TOOL_CALLS) -> int:
    next_call = used_calls + 1
    if next_call > max_calls:
        raise AgentToolLimitError(
            f"Tool call limit exceeded: attempted call {next_call} "
            f"but the maximum is {max_calls}. "
            f"Task stopped before executing tool '{tool_name}'."
        )
    return next_call


async def execute_tool_with_timeout(
    name: str,
    args: dict,
    cfg,
    cm,
    *,
    execute_tool,
    parse_tool_result,
    timeout_sec: int,
) -> object:
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(execute_tool, name, args, cfg, cm),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError as exc:
        raise AgentToolTimeoutError(
            f"Tool execution timed out: tool '{name}' exceeded "
            f"{timeout_sec} seconds. Task stopped."
        ) from exc
    return parse_tool_result(result)
