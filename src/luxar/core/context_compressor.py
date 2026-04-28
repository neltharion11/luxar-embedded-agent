"""Auto-compress conversation context when approaching model limits."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

COMPRESSION_PROMPT = (
    "Please provide a concise summary of the conversation above between a user and an AI assistant. "
    "Preserve all technical details, code snippets, file paths, configuration values, decisions made, "
    "and action results. Output only the summary text, no preamble."
)

MIN_KEEP_MESSAGES = 4


def estimate_tokens(text: str) -> int:
    """Conservative heuristic: ~2 chars per token for mixed Chinese/English content.
    Overestimates slightly to trigger compression earlier (safer side)."""
    return max(1, len(text) // 2)


def count_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "") or ""
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_tokens(part.get("text", ""))
        if m.get("tool_calls"):
            total += estimate_tokens(str(m.get("tool_calls", [])))
    return total


class ContextCompressor:
    def __init__(self, context_limit: int = 65536, threshold: float = 0.95):
        self.context_limit = context_limit
        self.threshold = threshold

    def should_compress(self, messages: list[dict]) -> bool:
        return count_tokens(messages) > int(self.context_limit * self.threshold)

    def compress(self, messages: list[dict], client: Any) -> list[dict]:
        limit = int(self.context_limit * self.threshold)
        if not messages or count_tokens(messages) <= limit:
            return messages

        keep_count = max(MIN_KEEP_MESSAGES, len(messages) - 2)

        while keep_count >= MIN_KEEP_MESSAGES:
            old_msgs = messages[:-keep_count] if keep_count < len(messages) else []
            recent = messages[-keep_count:]

            if not old_msgs:
                break

            old_text = _format_old_messages(old_msgs)
            if not old_text.strip():
                break

            try:
                resp = client.complete(prompt=old_text + "\n\n---\n\n" + COMPRESSION_PROMPT, system_prompt="")
                summary = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
            except Exception as e:
                logger.warning("Context compression failed, using truncated context: %s", e)
                summary = "[Previous conversation truncated]"

            compressed = [
                {"role": "system", "content": f"[Conversation summary]\n{summary}"},
            ] + recent

            if count_tokens(compressed) <= limit:
                logger.info("Context compressed: %d -> %d messages, ~%d tokens",
                            len(messages), len(compressed), count_tokens(compressed))
                return compressed

            keep_count -= 2

        return _hard_truncate(messages, limit)


def _format_old_messages(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "unknown")
        content = m.get("content", "") or ""
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        tool_call_id = m.get("tool_call_id", "")
        if tool_call_id:
            tag = f"tool({tool_call_id})"
        else:
            tag = role
        lines.append(f"[{tag}]: {content}")
    return "\n\n".join(lines)


def _hard_truncate(messages: list[dict], limit: int) -> list[dict]:
    result = []
    need_assistant_for = None
    for m in reversed(messages):
        # If we're about to add a tool message, ensure its preceding assistant is also kept
        if m.get("tool_call_id") or m.get("role") == "tool":
            need_assistant_for = m.get("tool_call_id")
        if need_assistant_for and m.get("role") == "assistant" and m.get("tool_calls"):
            need_assistant_for = None
        test = result + [m]
        if count_tokens(test) > limit and result and not need_assistant_for:
            break
        result = test
    result.reverse()
    if len(result) < len(messages):
        result.insert(0, {"role": "system", "content": "[Earlier conversation truncated due to context limit]"})
    return result
