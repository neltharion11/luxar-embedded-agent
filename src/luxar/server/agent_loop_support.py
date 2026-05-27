from __future__ import annotations

import json
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


def build_stream_tool_result_events(
    tool_name: str,
    args: dict,
    result: Any,
    *,
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
