from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass
class AgentLoopState:
    max_repair_attempts: int = 2
    repair_count: int = 0
    tool_calls_used: int = 0
    consecutive_failures: int = 0
    last_call_key: tuple = ()
    same_call_streak: int = 0


MAX_SAME_CALL_STREAK = 3


def check_same_call_loop(state: AgentLoopState, tool_name: str, args: dict) -> str | None:
    try:
        args_key = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        args_key = str(args)
    call_key = (tool_name, args_key)
    if call_key == state.last_call_key:
        state.same_call_streak += 1
    else:
        state.last_call_key = call_key
        state.same_call_streak = 1
    if state.same_call_streak > MAX_SAME_CALL_STREAK:
        return (
            f"Same tool call ({tool_name}) with identical arguments repeated "
            f"{state.same_call_streak} times — stopping to prevent infinite loop."
        )
    return None


def append_assistant_tool_call_message(
    api_messages: list[dict],
    conv: list[dict],
    tool_calls: list[dict],
    *,
    content: str | None,
    reasoning_content: str = "",
) -> dict:
    assistant_msg = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
    }
    if content:
        assistant_msg["content"] = content
    if tool_calls:
        assistant_msg["tool_calls"] = tool_calls
    if reasoning_content:
        assistant_msg["reasoning_content"] = reasoning_content
    api_messages.append(assistant_msg)
    conv.append(assistant_msg)
    return assistant_msg


def append_tool_result_message(
    api_messages: list[dict],
    conv: list[dict],
    *,
    tool_call_id: str,
    serialized_content: str,
) -> dict:
    tool_msg = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": serialized_content,
    }
    api_messages.append(tool_msg)
    conv.append(
        {
            "id": str(uuid.uuid4()),
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": serialized_content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return tool_msg


def append_final_assistant_message(
    conv: list[dict],
    *,
    content: str,
    reasoning_content: str = "",
) -> dict:
    final_message = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if reasoning_content:
        final_message["reasoning_content"] = reasoning_content
    conv.append(final_message)
    return final_message


def update_consecutive_failures(
    consecutive_failures: int,
    result: Any,
    *,
    is_failure: Callable[[Any], bool],
    limit: int,
    build_limit_message: Callable[[int], str],
) -> tuple[int, str | None]:
    if is_failure(result):
        consecutive_failures += 1
        if consecutive_failures >= limit:
            return consecutive_failures, build_limit_message(consecutive_failures)
        return consecutive_failures, None
    return 0, None


def extract_referenced_portions(llm_text: str, file_content: str) -> str | None:
    """Extract portions of file_content referenced by code blocks in llm_text.
    Returns matched snippets with context, or None if no matches found."""
    if not file_content or not llm_text:
        return None
    # Extract fenced code blocks (min 40 chars to avoid false matches)
    code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", llm_text, re.DOTALL)
    if not code_blocks:
        return None
    file_lines = file_content.split("\n")
    referenced: list[str] = []
    for block in code_blocks:
        block = block.strip()
        if len(block) < 40:
            continue
        first_line = block.split("\n")[0].strip()
        # Try to find this block in the file
        for i, line in enumerate(file_lines):
            if first_line in line:
                start = max(0, i - 2)
                block_lines = block.split("\n")
                end = min(len(file_lines), i + len(block_lines) + 2)
                snippet = "\n".join(file_lines[start:end])
                referenced.append(f"  ... (lines {start + 1}-{end})\n{snippet}")
                break
    if not referenced:
        return None
    return "\n\n---\n\n".join(referenced)


_VERBOSE_TOOLS = frozenset({"workspace_shell", "workspace_read_file", "workspace_write_file", "workspace_build"})


def _build_tool_output_summary(tool_name: str, args: dict, result: Any) -> tuple[str, str]:
    """Return (summary_line, content) for a verbose tool output."""
    data = result.data if hasattr(result, "data") else (result if isinstance(result, dict) else {})
    if tool_name == "workspace_shell":
        cmd = args.get("command", "?")
        stdout = data.get("stdout", "")
        return f"{cmd} → {len(stdout)} chars", stdout
    if tool_name == "workspace_read_file":
        path = args.get("path", "?")
        content_text = data.get("content", "")
        return f"{path} ({len(content_text)} chars)", content_text
    if tool_name == "workspace_build":
        if data.get("success", True):
            return "✅ Build succeeded", ""
        stderr = data.get("stderr", "")
        stdout = data.get("stdout", "")
        combined = (stderr + "\n" + stdout).strip()
        return "❌ Build failed", combined[:8000]
    if tool_name == "workspace_write_file":
        path = args.get("path", "?")
        content_text = args.get("content", "")
        return f"Wrote {path} ({len(content_text)} chars)", content_text[:8000]

    return f"{tool_name} completed", ""


def build_stream_tool_result_events(
    tool_name: str,
    args: dict,
    result: Any,
    *,
    tool_call_id: str = "",
    serialize_tool_data: Callable[[dict], str],
    format_summary: Callable[[str, dict, Any], tuple[bool, str]],
) -> list[dict]:
    events = [
        {
            "event": "tool_result",
            "data": json.dumps({"tool": tool_name, "result": serialize_tool_data(result.data)}, ensure_ascii=False),
        }
    ]
    if tool_name in {"skills_list", "skill_view", "skill_execute"}:
        events.append(
            {
                "event": "skill_loaded",
                "data": json.dumps({"tool": tool_name, "result": result.data}, ensure_ascii=False),
            }
        )
    if tool_name == "lesson_record":
        events.append(
            {
                "event": "lesson_recorded",
                "data": json.dumps(result.data, ensure_ascii=False),
            }
        )
    if tool_name in {"skill_promote", "lesson_promote"}:
        events.append(
            {
                "event": "promotion_applied",
                "data": json.dumps({"tool": tool_name, "result": result.data}, ensure_ascii=False),
            }
        )
    if tool_name in _VERBOSE_TOOLS:
        display_summary, display_content = _build_tool_output_summary(tool_name, args, result)
        if display_content:
            events.append(
                {
                    "event": "tool_output",
                    "data": json.dumps(
                        {"tool": tool_name, "tool_call_id": tool_call_id, "summary": display_summary, "content": display_content, "collapsed": True},
                        ensure_ascii=False,
                    ),
                }
            )

    ok, summary = format_summary(tool_name, args, result)
    events.append(
        {
            "event": "log",
            "data": json.dumps({"success": ok, "message": summary, "tool": tool_name}, ensure_ascii=False),
        }
    )
    return events


def build_reasoning_handoff_retry_events(attempt: int, max_attempts: int) -> list[dict]:
    return [
        {
            "event": "reset_output",
            "data": json.dumps({"reason": "reasoning_handoff_retry"}),
        },
        {
            "event": "warning",
            "data": json.dumps(
                {
                    "warning": (
                        f"Recovered from stale reasoning context and retried "
                        f"(attempt {attempt}/{max_attempts})."
                    )
                }
            ),
        },
    ]
