from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from luxar.core.config_manager import ConfigManager, LLMSection
from luxar.core.document_engineering import DocumentEngineeringAnalyzer
from luxar.core.project_manager import ProjectManager
from luxar.core.firmware_library_manager import FirmwareLibraryManager
from luxar.core.git_manager import GitManager
from luxar.core.review_engine import ReviewEngine
from luxar.core.conversation_store import ConversationStore
from luxar.core.context_compressor import ContextCompressor, count_tokens
from luxar.core.llm_client import _OPENAI_PROVIDERS
from luxar.tools.memory_tool import (
    memory_lesson_promote,
    memory_lesson_record,
    memory_lessons,
    memory_read,
    memory_search,
    memory_write,
)
from luxar.tools.runtime_tool import explain_runtime_tool, run_runtime
from luxar.tools.skills_tool import (
    skill_execute,
    skill_manage,
    skill_promote,
    skill_view,
    skills_list as vnext_skills_list,
)
from luxar.tools.init_project import run_init_project
from luxar.tools.workspace_tool import (
    workspace_build,
    workspace_flash,
    workspace_inspect,
    workspace_monitor,
    workspace_probe,
)


# ===== Tool Definitions (OpenAI Function Calling schema) =====

MAX_AGENT_TOOL_CALLS = 20
MAX_AGENT_TOOL_TIMEOUT_SEC = 180
_TOOL_TIMEOUT_OVERRIDES: dict[str, int] = {
    "workspace_build": 300,
    "workspace_flash": 300,
}


class AgentToolLimitError(RuntimeError):
    """Raised when the agent exceeds the allowed number of tool calls."""


class AgentToolTimeoutError(RuntimeError):
    """Raised when a single tool call exceeds the allowed runtime."""

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "runtime_run",
            "description": "Run the LUXAR v0.2.0 runtime for a task inside the current workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Natural-language task description"},
                    "project": {"type": "string", "description": "Optional project name"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "runtime_explain",
            "description": "Explain the LUXAR v0.2.0 runtime model and current orchestration approach.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skills_list",
            "description": "List available runtime skills, optionally filtered by category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Optional skill category filter"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_view",
            "description": "View a single runtime skill by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_manage",
            "description": "Create, edit, patch, or archive a runtime skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "create, edit, patch, or archive"},
                    "name": {"type": "string", "description": "Skill name"},
                    "category": {"type": "string", "description": "Skill category"},
                    "content": {"type": "string", "description": "Replacement or creation content"},
                    "old_string": {"type": "string", "description": "Patch target text"},
                    "new_string": {"type": "string", "description": "Patch replacement text"},
                },
                "required": ["action", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_promote",
            "description": "Promote a runtime skill to a higher promotion level.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                    "category": {"type": "string", "description": "Optional skill category"},
                    "promotion_level": {"type": "string", "description": "Target promotion level"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_execute",
            "description": "Execute an executable runtime skill and collect evidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                    "category": {"type": "string", "description": "Optional skill category"},
                    "project": {"type": "string", "description": "Project name"},
                    "port": {"type": "string", "description": "Optional serial port"},
                    "baudrate": {"type": "integer", "description": "Optional monitor baudrate"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": "Read durable memory or user memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "memory or user"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": "Write durable memory or user memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to write"},
                    "target": {"type": "string", "description": "memory or user"},
                    "append": {"type": "boolean", "description": "Append when true; replace when false"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Search memory, lessons, and recall context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lesson_list",
            "description": "List recorded lessons.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lesson_search",
            "description": "Search recorded lessons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Maximum number of results"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lesson_record",
            "description": "Record a lesson candidate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "payload": {"type": "object", "description": "Lesson payload"},
                    "promoted": {"type": "boolean", "description": "Store directly as promoted"},
                },
                "required": ["payload"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lesson_promote",
            "description": "Promote a lesson into promoted state with evidence count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string", "description": "Lesson slug"},
                    "evidence_count": {"type": "integer", "description": "Evidence count"},
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_inspect",
            "description": "Inspect the runtime workspace layout and roots.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_build",
            "description": "Build a project through the workspace runtime primitive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "clean": {"type": "boolean", "description": "Whether to clean first"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_flash",
            "description": "Flash a project through the workspace runtime primitive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "probe": {"type": "string", "description": "Optional probe"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_monitor",
            "description": "Monitor a project through the workspace runtime primitive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "port": {"type": "string", "description": "Serial port"},
                    "baudrate": {"type": "integer", "description": "Baudrate"},
                },
                "required": ["project", "port"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "workspace_probe",
            "description": "Run a workspace probe primitive such as i2c.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "probe_type": {"type": "string", "description": "Probe type"},
                },
                "required": ["project"],
            },
        },
    },
]

MAX_CONSECUTIVE_TOOL_FAILURES = 3


_agent_log = logging.getLogger("luxar.agent")

class ToolExecutionEnvelope(BaseModel):
    ok: bool
    tool: str
    data: Any
    error: str = ""
    summary_source: dict = Field(default_factory=dict)
    truncated: bool = False


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    return value


def _build_tool_envelope(
    name: str,
    data: Any | None = None,
    *,
    error: str = "",
    summary_source: dict | None = None,
    truncated: bool = False,
) -> ToolExecutionEnvelope:
    payload = _to_jsonable(data if data is not None else {})
    source = _to_jsonable(summary_source if summary_source is not None else payload)
    envelope = ToolExecutionEnvelope(
        ok=not _is_tool_result_failure(payload),
        tool=name,
        data=payload,
        error=error or (payload.get("error", "") if isinstance(payload, dict) else ""),
        summary_source=source if isinstance(source, dict) else {},
        truncated=truncated,
    )
    if envelope.error and not isinstance(envelope.data, dict):
        envelope.data = {"result": envelope.data, "error": envelope.error}
    return envelope


def _parse_tool_result(result: Any) -> ToolExecutionEnvelope:
    if isinstance(result, ToolExecutionEnvelope):
        return result
    if isinstance(result, dict) and {"ok", "tool", "data", "error", "summary_source", "truncated"} <= set(result.keys()):
        return ToolExecutionEnvelope(**result)
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return ToolExecutionEnvelope(
                ok=False,
                tool="unknown",
                data={"error": "unparseable tool result", "raw_result": result},
                error="unparseable tool result",
                summary_source={"error": "unparseable tool result"},
                truncated=True,
            )
        return _build_tool_envelope("unknown", payload)
    return _build_tool_envelope("unknown", result)


def _compact_tool_payload(
    value: Any,
    *,
    aggressive: bool = False,
    parent_key: str = "",
    truncation_state: dict[str, bool] | None = None,
) -> Any:
    if truncation_state is None:
        truncation_state = {"truncated": False}

    string_limits = {
        "diff": 1200 if aggressive else 4000,
        "stdout": 1000 if aggressive else 2500,
        "stderr": 1000 if aggressive else 2500,
        "raw_response": 1000 if aggressive else 2000,
    }
    default_string_limit = 500 if aggressive else 1500
    list_limits = {"skills": 5 if aggressive else 20}
    default_list_limit = 10 if aggressive else 40
    dict_limit = 20 if aggressive else 60

    if isinstance(value, str):
        limit = string_limits.get(parent_key, default_string_limit)
        if len(value) > limit:
            truncation_state["truncated"] = True
            return value[:limit] + f"\n... [truncated from {len(value)} chars]"
        return value

    if isinstance(value, list):
        limit = list_limits.get(parent_key, default_list_limit)
        items = value[:limit]
        if len(value) > limit:
            truncation_state["truncated"] = True
        return [
            _compact_tool_payload(item, aggressive=aggressive, parent_key=parent_key, truncation_state=truncation_state)
            for item in items
        ]

    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > dict_limit:
            truncation_state["truncated"] = True
            items = items[:dict_limit]
        return {
            key: _compact_tool_payload(item, aggressive=aggressive, parent_key=str(key), truncation_state=truncation_state)
            for key, item in items
        }

    return value


def _serialize_tool_data(data: Any) -> str:
    return json.dumps(_to_jsonable(data), ensure_ascii=False)


def _serialize_tool_content_for_llm(envelope: ToolExecutionEnvelope, max_chars: int = 3000) -> str:
    truncation_state = {"truncated": False}
    compacted = _compact_tool_payload(copy.deepcopy(envelope.data), truncation_state=truncation_state)
    text = _serialize_tool_data(compacted)
    if len(text) <= max_chars:
        return text

    truncation_state = {"truncated": False}
    compacted = _compact_tool_payload(
        copy.deepcopy(envelope.summary_source or envelope.data),
        aggressive=True,
        truncation_state=truncation_state,
    )
    text = _serialize_tool_data(compacted)
    if len(text) <= max_chars:
        return text

    fallback = {
        "success": envelope.ok,
        "tool": envelope.tool,
        "error": envelope.error,
        "truncated": True,
        "summary_source": _compact_tool_payload(
            copy.deepcopy(envelope.summary_source or envelope.data),
            aggressive=True,
            truncation_state={"truncated": False},
        ),
    }
    text = _serialize_tool_data(fallback)
    if len(text) <= max_chars:
        return text

    minimal = {
        "success": envelope.ok,
        "tool": envelope.tool,
        "error": envelope.error[:500] if envelope.error else "",
        "truncated": True,
    }
    return _serialize_tool_data(minimal)


def _format_tool_result_summary(name: str, args: dict, result: Any) -> tuple[bool, str]:
    """Generate a human-readable one-line summary of a tool result.
    Returns (success: bool, message: str)."""
    envelope = _parse_tool_result(result)
    if envelope.tool == "unknown":
        envelope.tool = name
    data = envelope.summary_source or (envelope.data if isinstance(envelope.data, dict) else {})
    is_ok = envelope.ok

    if name == "runtime_run":
        selected = len(data.get("selected_skills") or [])
        executable = len(data.get("selected_executable_skills") or [])
        msg = f"runtime 规划完成: {selected} 个技能"
        if executable:
            msg += f", {executable} 个可执行技能"
        return is_ok, msg if is_ok else f"runtime 失败: {data.get('error') or data.get('message', '')[:80]}"

    if name == "runtime_explain":
        return is_ok, "runtime 模型已解释" if is_ok else f"runtime 解释失败: {data.get('error','')}"

    if name == "skills_list":
        skills = data.get("skills") or []
        return is_ok, f"已加载 {len(skills)} 个技能"

    if name == "skill_view":
        skill = data.get("skill") or {}
        title = skill.get("name") or data.get("name") or "技能"
        return is_ok, f"已查看技能: {title}" if is_ok else f"技能查看失败: {data.get('error','')}"

    if name == "skill_manage":
        return is_ok, f"技能已{data.get('action', '处理')}" if is_ok else f"技能处理失败: {data.get('error','')}"

    if name == "skill_promote":
        level = data.get("promotion_level") or "validated"
        return is_ok, f"技能已晋升为 {level}" if is_ok else f"技能晋升失败: {data.get('error','')}"

    if name == "skill_execute":
        evidence = data.get("evidence") or []
        return is_ok, f"技能执行完成: {len(evidence)} 条证据" if is_ok else f"技能执行失败: {data.get('error','')}"

    if name == "memory_read":
        return is_ok, "记忆已读取" if is_ok else f"记忆读取失败: {data.get('error','')}"

    if name == "memory_write":
        return is_ok, "记忆已更新" if is_ok else f"记忆写入失败: {data.get('error','')}"

    if name == "memory_search":
        results = data.get("results") or []
        return is_ok, f"召回到 {len(results)} 条上下文"

    if name == "lesson_list":
        lessons = data.get("lessons") or []
        return is_ok, f"已列出 {len(lessons)} 条经验"

    if name == "lesson_search":
        lessons = data.get("lessons") or []
        return is_ok, f"匹配到 {len(lessons)} 条经验"

    if name == "lesson_record":
        lesson = data.get("lesson") or {}
        topic = lesson.get("topic") or lesson.get("slug") or "lesson"
        return is_ok, f"已记录经验: {topic}" if is_ok else f"经验记录失败: {data.get('error','')}"

    if name == "lesson_promote":
        return is_ok, "经验已晋升" if is_ok else f"经验晋升失败: {data.get('error','')}"

    if name == "workspace_build":
        if is_ok:
            return True, "构建成功"
        stderr = data.get("stderr") or ""
        if "error" in stderr.lower():
            errors = stderr.lower().count("error:")
            return False, f"构建失败: {errors} 个错误"
        error_msg = data.get("error") or data.get("message", "") or "编译出错"
        return False, f"构建失败: {str(error_msg)[:80]}"

    if name == "workspace_flash":
        return (is_ok, "烧录成功" if is_ok else f"烧录失败: {data.get('error','')}")

    if name == "workspace_monitor":
        return True, "串口监控已启动"

    if name == "workspace_probe":
        probe_type = data.get("probe_type") or args.get("probe_type", "")
        return is_ok, f"{probe_type or 'workspace'} 探测已执行"

    if name == "workspace_inspect":
        return is_ok, "工作区状态已读取"

    # Generic fallback for unknown tools
    if is_ok:
        return True, f"工具 '{name}' 已完成"
    error_msg = data.get("error") or data.get("message", "")
    return False, f"工具 '{name}' 返回失败: {str(error_msg)[:80]}"


def _is_tool_result_failure(result: Any) -> bool:
    """Check whether a JSON tool result indicates a failure.
    Re-entry blocked results are NOT counted as failures."""
    if isinstance(result, ToolExecutionEnvelope):
        data = result.data
        if isinstance(data, dict) and data.get("blocked") is True:
            return False
        if result.error:
            return True
        if result.ok is False:
            return True
        return _is_tool_result_failure(data)
    if isinstance(result, str):
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return True
    else:
        data = result
    if isinstance(data, dict):
        if data.get("blocked") is True:
            return False
        if data.get("success") is False:
            return True
        if "error" in data:
            return True
        if data.get("status") in ("failed", "error"):
            return True
    return False


def _build_consecutive_failure_limit_message(failures: int) -> str:
    return (
        f"连续 {failures} 次工具调用返回了失败结果。"
        f"我停止继续尝试，以免造成更多问题。请检查项目状态并告诉我下一步怎么做。"
    )


def _execute_tool(name: str, args: dict, cfg: Any, cm: ConfigManager) -> ToolExecutionEnvelope:
    ws = cm.workspace_root()
    project = args.get("project", "")
    project_path = ws / project if project else None
    kb_root = cm.driver_library_root() / "knowledge_base"

    try:
        if name == "runtime_run":
            return _build_tool_envelope(name, run_runtime(task=args.get("task", ""), project=args.get("project", "")))

        if name == "runtime_explain":
            return _build_tool_envelope(name, explain_runtime_tool())

        if name == "skills_list":
            return _build_tool_envelope(name, vnext_skills_list(category=args.get("category")))

        if name == "skill_view":
            return _build_tool_envelope(name, skill_view(name=args.get("name", "")))

        if name == "skill_manage":
            return _build_tool_envelope(
                name,
                skill_manage(
                    action=args.get("action", ""),
                    name=args.get("name", ""),
                    category=args.get("category", "workflows"),
                    content=args.get("content", ""),
                    old_string=args.get("old_string", ""),
                    new_string=args.get("new_string", ""),
                ),
            )

        if name == "skill_promote":
            return _build_tool_envelope(
                name,
                skill_promote(
                    name=args.get("name", ""),
                    category=args.get("category", ""),
                    promotion_level=args.get("promotion_level", "validated"),
                ),
            )

        if name == "skill_execute":
            return _build_tool_envelope(
                name,
                skill_execute(
                    name=args.get("name", ""),
                    category=args.get("category", ""),
                    project=args.get("project", ""),
                    port=args.get("port", ""),
                    baudrate=int(args.get("baudrate", 115200)),
                ),
            )

        if name == "memory_read":
            return _build_tool_envelope(name, memory_read(target=args.get("target", "memory")))

        if name == "memory_write":
            return _build_tool_envelope(
                name,
                memory_write(
                    content=args.get("content", ""),
                    target=args.get("target", "memory"),
                    append=bool(args.get("append", True)),
                ),
            )

        if name == "memory_search":
            return _build_tool_envelope(name, memory_search(query=args.get("query", "")))

        if name == "lesson_list":
            return _build_tool_envelope(name, memory_lessons())

        if name == "lesson_search":
            return _build_tool_envelope(
                name,
                memory_lessons(query=args.get("query", ""), limit=int(args.get("limit", 5))),
            )

        if name == "lesson_record":
            return _build_tool_envelope(
                name,
                memory_lesson_record(payload=args.get("payload", {}) or {}, promoted=bool(args.get("promoted", False))),
            )

        if name == "lesson_promote":
            return _build_tool_envelope(
                name,
                memory_lesson_promote(slug=args.get("slug", ""), evidence_count=int(args.get("evidence_count", 1))),
            )

        if name == "workspace_inspect":
            return _build_tool_envelope(name, workspace_inspect())

        if name == "workspace_build":
            return _build_tool_envelope(
                name,
                workspace_build(project=args.get("project", ""), clean=bool(args.get("clean", False))),
            )

        if name == "workspace_flash":
            return _build_tool_envelope(
                name,
                workspace_flash(project=args.get("project", ""), probe=args.get("probe", "")),
            )

        if name == "workspace_monitor":
            return _build_tool_envelope(
                name,
                workspace_monitor(
                    project=args.get("project", ""),
                    port=args.get("port", ""),
                    baudrate=int(args.get("baudrate", 115200)),
                ),
            )

        if name == "workspace_probe":
            return _build_tool_envelope(
                name,
                workspace_probe(project=args.get("project", ""), probe_type=args.get("probe_type", "i2c")),
            )

        return _build_tool_envelope(name, {"error": f"Unknown tool: {name}"}, error=f"Unknown tool: {name}")
    except Exception as e:
        message = f"Tool '{name}' failed: {e}"
        return _build_tool_envelope(name, {"error": message}, error=message)


def _enforce_tool_call_budget(tool_name: str, used_calls: int) -> int:
    next_call = used_calls + 1
    if next_call > MAX_AGENT_TOOL_CALLS:
        raise AgentToolLimitError(
            f"Tool call limit exceeded: attempted call {next_call} "
            f"but the maximum is {MAX_AGENT_TOOL_CALLS}. "
            f"Task stopped before executing tool '{tool_name}'."
        )
    return next_call


async def _execute_tool_with_limits(
    name: str,
    args: dict,
    cfg: Any,
    cm: ConfigManager,
    *,
    used_calls: int,
) -> tuple[ToolExecutionEnvelope, int]:
    next_call = _enforce_tool_call_budget(name, used_calls)
    result = await _execute_tool_with_timeout(name, args, cfg, cm)
    return result, next_call


async def _execute_tool_with_timeout(name: str, args: dict, cfg: Any, cm: ConfigManager) -> ToolExecutionEnvelope:
    timeout_sec = _TOOL_TIMEOUT_OVERRIDES.get(name, MAX_AGENT_TOOL_TIMEOUT_SEC)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_execute_tool, name, args, cfg, cm),
            timeout=timeout_sec,
        )
    except asyncio.TimeoutError as exc:
        raise AgentToolTimeoutError(
            f"Tool execution timed out: tool '{name}' exceeded "
            f"{timeout_sec} seconds. Task stopped."
        ) from exc
    return _parse_tool_result(result)


def _build_tool_running_payload(name: str, args: dict) -> dict:
    payload = {"tool": name}
    task = args.get("task", "")
    project = args.get("project", "")
    if task:
        payload["task"] = task
    if project:
        payload["project"] = project
    return payload


def _build_active_project_init_block_result(project: str, args: dict) -> dict:
    active_project = _normalize_project_name(project)
    requested_project = args.get("name", "")
    return {
        "success": True,
        "blocked": True,
        "reason": "init_project_blocked_active_project",
        "project": active_project,
        "requested_project": requested_project,
        "message": (
            f"当前已经在项目 '{active_project}' 中，不能在项目会话里调用 init_project。"
            "请继续使用当前项目；如需新建项目，请回到全局会话或使用新建项目入口。"
        ),
    }


# ===== Persistent conversation store =====

class ChatMessage(BaseModel):
    id: str = ""
    role: str
    content: str
    created_at: str = ""


_conv_store: ConversationStore | None = None
_conv_cache: dict[str, list[dict]] = {}


def _get_conv(project: str) -> list[dict]:
    if project not in _conv_cache:
        _conv_cache[project] = _conv_store.load(project) if _conv_store else []
    return _conv_cache[project]


def _save_conv(project: str):
    if _conv_store and project in _conv_cache:
        _conv_store.save(project, _conv_cache[project])


# ===== Agent Loop: LLM reasoning + tool execution =====

SYSTEM_PROMPT_TEMPLATE = """\
You are LUXAR v0.2.0 operating inside project '{project}'.

- Respond in the same language as the user.
- Harness is the runtime behavior system. Use runtime, skills, memory, and workspace primitives only.
- Do not fabricate tool output, build status, flash status, probe results, or hardware state.
- Treat skills as the only procedural artifact. Prefer loading, executing, patching, and promoting skills over inventing ad-hoc workflows.
- Use memory and lessons for recall and self-improvement. Record failures as lessons before assuming a reusable skill update.
- Use workspace primitives for concrete actions like inspect, build, flash, monitor, and probe.
- If the task cannot be completed with current evidence, explain the blocker instead of pretending success.
- Keep explanations concise and action-oriented.
"""

GLOBAL_SYSTEM_PROMPT = """\
You are LUXAR v0.2.0.

- Respond in the same language as the user.
- Harness is the runtime behavior system. Use runtime, skills, memory, and workspace primitives only.
- Skills are the only procedural artifacts. Memory stores stable facts. Lessons store unpromoted experience.
- Do not fabricate evidence, hardware state, or tool results.
- For casual conversation or explanation-only requests, respond directly without tools.
- For concrete actions, use the smallest appropriate primitive and summarize the evidence-backed result.
"""


PROJECT_TEMPLATE_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("项目名", "project name", "name"),
    "mcu": ("mcu",),
    "platform": ("平台", "platform"),
    "runtime": ("运行时", "runtime"),
    "firmware_package": ("固件包", "firmware package"),
    "target": ("目标功能", "target behavior", "target function"),
    "peripherals": ("外设/通信", "peripherals / buses", "peripherals", "buses"),
    "reference_docs": ("参考文档", "reference docs", "reference documents"),
}


def _normalize_project_name(project: str) -> str:
    return "" if project in {"", "__global__"} else project


def _normalize_template_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().lower())


def _match_template_field(label: str) -> str:
    normalized = _normalize_template_label(label)
    for field, aliases in PROJECT_TEMPLATE_ALIASES.items():
        for alias in aliases:
            if normalized == _normalize_template_label(alias):
                return field
    return ""


def _parse_project_creation_request(message: str) -> dict[str, str] | None:
    parsed: dict[str, str] = {}
    seen_values: dict[str, set[str]] = {}
    labeled_lines = 0
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^\s*[-*]?\s*([^:：]+?)\s*[:：]\s*(.+?)\s*$", line)
        if not match:
            continue
        field = _match_template_field(match.group(1))
        if not field:
            continue
        value = match.group(2).strip()
        if not value:
            continue
        labeled_lines += 1
        seen_values.setdefault(field, set()).add(value)
        if field not in parsed:
            parsed[field] = value

    if labeled_lines < 2:
        return None
    if "name" not in parsed or "mcu" not in parsed:
        return None
    if len(seen_values.get("name", set())) > 1:
        return {
            "error": "检测到多个不同的项目名。请一次只提交一个项目创建模板。",
        }
    return parsed


def _build_project_creation_summary(project_payload: dict[str, str], created_name: str) -> str:
    parts = [
        f"项目 `{created_name}` 已创建。",
        f"MCU: {project_payload.get('mcu', '')}",
        f"平台: {project_payload.get('platform', 'stm32cubemx')}",
        f"运行时: {project_payload.get('runtime', 'baremetal')}",
        f"固件包: {project_payload.get('firmware_package', 'STM32Cube_FW_F1')}",
    ]
    if project_payload.get("target"):
        parts.append(f"目标功能: {project_payload['target']}")
    if project_payload.get("peripherals"):
        parts.append(f"外设/通信: {project_payload['peripherals']}")
    if project_payload.get("reference_docs"):
        parts.append(f"参考文档: {project_payload['reference_docs']}")
    return "\n".join(parts)


def _get_context_limit(cfg: Any) -> int:
    """Return the context window size for the configured provider + model, default 4096."""
    provider = cfg.llm.provider.strip().lower()
    model = cfg.llm.model
    info = _OPENAI_PROVIDERS.get(provider, {})
    for m in info.get("models", []):
        if m["id"] == model:
            return m.get("context", 4096)
    if provider == "claude":
        return 200000
    return 4096


def _inject_environment_info(base_prompt: str, cm: ConfigManager) -> str:
    """Tell the agent what tools/paths are actually available to prevent hallucination."""
    from luxar.core.toolchain_manager import ToolchainManager
    cfg = cm.ensure_default_config()
    tcs = ToolchainManager(cfg, project_root=str(cm.project_root()))
    status = tcs.status()
    lines = []
    if status.get("cmake"):
        lines.append(f"- cmake: {status['cmake']}")
    if status.get("arm_gcc"):
        lines.append(f"- arm-none-eabi-gcc: {status['arm_gcc']}")
    if status.get("ninja"):
        lines.append(f"- ninja: {status['ninja']}")
    if not lines:
        return base_prompt
    return base_prompt + "\n\n## Available Toolchains (already configured on this machine)\n" + "\n".join(lines) + \
           "\nDo NOT claim toolchains are missing — they are available at these paths."


def _enrich_system_prompt(base_prompt: str, msg_content: str, docs: list | None = None,
                          project: str = "") -> str:
    """Search memory store for relevant past conversations and inject into prompt."""
    enriched = base_prompt
    if docs:
        enriched += f"\n\nThe user has attached documents: {', '.join(docs)}.\nUse analyze_document_engineering to extract facts from them if needed.\n"
    if not _conv_store or not project:
        return enriched
    try:
        related = _conv_store.search(query=msg_content, project=project or None, limit=3)
    except Exception:
        return enriched
    if not related:
        return enriched
    lines = ["\n## Relevant history from your past conversations"]
    for r in related:
        role = r.get("role", "?")
        content = (r.get("content", "") or "")[:200]
        lines.append(f"- [{role}]: {content}")
    return enriched + "\n" + "\n".join(lines) + "\n"


def _truncate_with_tool_pairing(conv: list[dict], max_keep: int = 20) -> list[dict]:
    """Take last `max_keep` messages, but extend window backward to include
    tool results for any assistant tool_calls that fall within the window.
    Prevents orphan tool_calls→no tool message API rejection."""
    if len(conv) <= max_keep:
        return list(conv)
    start = len(conv) - max_keep
    # Collect tool_call_ids from assistant messages in the window
    orphan_ids: set[str] = set()
    for m in conv[start:]:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                tc_id = isinstance(tc, dict) and tc.get("id") or tc
                if tc_id:
                    orphan_ids.add(tc_id)
    # Remove IDs that already have matching tool messages in the window
    for m in conv[start:]:
        tc_id = m.get("tool_call_id")
        if tc_id:
            orphan_ids.discard(tc_id)
    # Extend window backward to grab missing tool results
    if orphan_ids:
        for i in range(start - 1, -1, -1):
            tc_id = conv[i].get("tool_call_id")
            if tc_id and tc_id in orphan_ids:
                orphan_ids.discard(tc_id)
                start = i
                if not orphan_ids:
                    break
    return list(conv[start:])


def _prepare_agent_context(
    conv: list[dict],
    msg_content: str,
    project: str,
    cfg: Any,
    cm: ConfigManager,
    client: Any,
    docs: list | None = None,
) -> list[dict]:
    """Build the API messages array with system prompt, memory enrichment, and context compression."""
    system_prompt = (SYSTEM_PROMPT_TEMPLATE.format(project=project) if project
                     else GLOBAL_SYSTEM_PROMPT)
    system_prompt = _enrich_system_prompt(system_prompt, msg_content, docs, project)
    system_prompt = _inject_environment_info(system_prompt, cm)

    ctx_limit = _get_context_limit(cfg)
    compressor = ContextCompressor(context_limit=ctx_limit)
    if compressor.should_compress(conv):
        conv[:] = compressor.compress(conv, client)

    api_messages: list[dict] = [{"role": "system", "content": system_prompt}]
    recent = _truncate_with_tool_pairing(conv, max_keep=20)
    for m in recent:
        entry: dict = {"role": m["role"], "content": m["content"]}
        if m.get("tool_call_id"):
            entry["role"] = "tool"
            entry["tool_call_id"] = m["tool_call_id"]
            # Ensure preceding message is an assistant with tool_calls (API requirement)
            tc_fix = [{
                "id": m["tool_call_id"],
                "type": "function",
                "function": {"name": m.get("tool_name", "unknown"), "arguments": "{}"}
            }]
            if api_messages and api_messages[-1]["role"] != "tool":
                if api_messages[-1]["role"] == "assistant" and "tool_calls" not in api_messages[-1]:
                    api_messages[-1]["tool_calls"] = tc_fix
                elif api_messages[-1]["role"] != "assistant":
                    api_messages.append({"role": "assistant", "content": None, "tool_calls": tc_fix})
            else:
                # Previous message was also a tool → insert assistant between them
                api_messages.insert(len(api_messages) - 1, {"role": "assistant", "content": None, "tool_calls": tc_fix})
        if m.get("tool_calls"):
            entry["tool_calls"] = m["tool_calls"]
        if m.get("reasoning_content"):
            entry["reasoning_content"] = m["reasoning_content"]
        api_messages.append(entry)
    return _validate_api_messages(api_messages)


def _validate_api_messages(msgs: list[dict]) -> list[dict]:
    """Final pass: ensure every tool message is preceded by assistant with tool_calls
    AND every assistant's tool_calls have matching tool results.
    This is a safety net — handles edge cases from compression, truncation, old data, etc."""
    clean: list[dict] = []
    for m in msgs:
        if m["role"] == "tool" and m.get("tool_call_id"):
            if not clean or clean[-1]["role"] != "assistant":
                clean.append({
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": m["tool_call_id"], "type": "function",
                        "function": {"name": m.get("tool_name", "unknown"), "arguments": "{}"}
                    }]
                })
            elif "tool_calls" not in clean[-1]:
                clean[-1]["tool_calls"] = [{
                    "id": m["tool_call_id"], "type": "function",
                    "function": {"name": m.get("tool_name", "unknown"), "arguments": "{}"}
                }]
        clean.append(m)
    return _strip_orphan_tool_calls(clean)


def _strip_orphan_tool_calls(msgs: list[dict]) -> list[dict]:
    """Remove tool_calls from assistant messages that have no matching tool result.
    DeepSeek API rejects messages where tool_call_ids lack corresponding tool messages."""
    known_tool_ids: set[str] = {
        m["tool_call_id"]
        for m in msgs
        if m["role"] == "tool" and m.get("tool_call_id")
    }
    for m in msgs:
        if m["role"] == "assistant" and m.get("tool_calls"):
            filtered = [tc for tc in m["tool_calls"] if tc.get("id") in known_tool_ids]
            if filtered:
                m["tool_calls"] = filtered
            else:
                del m["tool_calls"]
    return msgs


def _is_reasoning_handoff_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "reasoning_content" in message and "must be passed back" in message


def _sanitize_reasoning_message(message: dict) -> dict:
    clean = dict(message)
    if not clean.get("reasoning_content"):
        clean.pop("reasoning_content", None)
    return clean


def _repair_messages_for_reasoning_handoff(api_messages: list[dict], aggressive: bool = False) -> list[dict]:
    """Best-effort recovery for older chats that lost assistant reasoning state.

    Some providers require assistant reasoning_content to be echoed back exactly.
    If an older non-stream response stored assistant content without reasoning_content,
    we drop those incomplete assistant/tool turns. In aggressive mode, we keep only
    the system prompt plus recent user messages, sacrificing history to keep chat usable.
    """
    if aggressive:
        repaired = []
        for index, message in enumerate(api_messages):
            clean = _sanitize_reasoning_message(message)
            if index == 0 or clean.get("role") == "user":
                repaired.append(clean)
        if repaired:
            user_messages = [msg for msg in repaired[1:] if msg.get("role") == "user"]
            repaired = [repaired[0]] + user_messages[-6:]
        return repaired

    repaired: list[dict] = []
    dropped_tool_call_ids: set[str] = set()
    for index, message in enumerate(api_messages):
        clean = _sanitize_reasoning_message(message)
        if index == 0:
            repaired.append(clean)
            continue
        if clean.get("role") == "assistant" and not clean.get("reasoning_content"):
            for tool_call in clean.get("tool_calls") or []:
                tool_id = tool_call.get("id")
                if tool_id:
                    dropped_tool_call_ids.add(tool_id)
            continue
        if clean.get("role") == "tool" and clean.get("tool_call_id") in dropped_tool_call_ids:
            continue
        repaired.append(clean)
    return _validate_api_messages(repaired)


def _retry_after_reasoning_handoff_repair(client: Any, api_messages: list[dict]) -> tuple[Any | None, list[dict], Exception | None]:
    for aggressive in (False, True):
        repaired = _repair_messages_for_reasoning_handoff(api_messages, aggressive=aggressive)
        try:
            return client.complete_with_tools(messages=repaired, tools=TOOLS), repaired, None
        except Exception as retry_exc:
            if not _is_reasoning_handoff_error(retry_exc):
                return None, repaired, retry_exc
    return None, api_messages, None


def _create_project_from_template(project_payload: dict[str, str], cfg: Any, cm: ConfigManager):
    platform = (project_payload.get("platform", "stm32cubemx") or "stm32cubemx").strip().lower()
    runtime = (project_payload.get("runtime", "baremetal") or "baremetal").strip().lower()
    if platform not in {"stm32cubemx", "stm32firmware"}:
        raise ValueError("平台必须是 stm32cubemx 或 stm32firmware。")
    if runtime not in {"baremetal", "freertos"}:
        raise ValueError("运行时必须是 baremetal 或 freertos。")

    return run_init_project(
        workspace=str(cm.workspace_root()),
        name=project_payload["name"].strip(),
        mcu=project_payload["mcu"].strip(),
        platform=platform,
        runtime=runtime,
        project_mode="cubemx" if platform == "stm32cubemx" else "firmware",
        firmware_package=(project_payload.get("firmware_package", "") or cfg.stm32.firmware_package).strip(),
    )


async def _stream_project_template_creation(
    conv: list[dict],
    project_payload: dict[str, str],
    cfg: Any,
    cm: ConfigManager,
    storage_project: str,
):
    tool_call_id = f"call-{uuid.uuid4()}"
    tool_args = json.dumps(
        {
            "name": project_payload["name"].strip(),
            "mcu": project_payload["mcu"].strip(),
            "platform": (project_payload.get("platform", "stm32cubemx") or "stm32cubemx").strip().lower(),
            "runtime": (project_payload.get("runtime", "baremetal") or "baremetal").strip().lower(),
            "firmware_package": (project_payload.get("firmware_package", "") or cfg.stm32.firmware_package).strip(),
        },
        ensure_ascii=False,
    )
    yield {"event": "tool_call", "data": json.dumps({"tool_call": "init_project"})}

    assistant_tool_msg = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": tool_call_id,
            "type": "function",
            "function": {"name": "init_project", "arguments": tool_args},
        }],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    conv.append(assistant_tool_msg)
    yield {"event": "tool_running", "data": json.dumps({"tool": "init_project"})}

    try:
        project = _create_project_from_template(project_payload, cfg, cm)
    except Exception as exc:
        yield {"event": "error", "data": json.dumps({"error": str(exc)})}
        _save_conv(storage_project)
        return

    project_result = project.model_dump(mode="json") if hasattr(project, "model_dump") else project
    tool_envelope = _build_tool_envelope("init_project", project_result)
    tool_result = _serialize_tool_data(tool_envelope.data)
    conv.append({
        "id": str(uuid.uuid4()),
        "role": "tool",
        "tool_call_id": tool_call_id,
        "tool_name": "init_project",
        "content": _serialize_tool_content_for_llm(tool_envelope),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    yield {"event": "tool_result", "data": json.dumps({"tool": "init_project", "result": tool_result})}
    ok, summary = _format_tool_result_summary("init_project", {}, tool_envelope)
    _agent_log.info("init_project → %s", summary) if ok else _agent_log.warning("init_project → %s", summary)

    summary = _build_project_creation_summary(project_payload, project_payload["name"].strip())
    conv.append({
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "content": summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_conv(storage_project)
    yield {"event": "token", "data": json.dumps({"token": summary})}
    yield {"event": "done", "data": "[DONE]"}


async def _run_agent_loop(
    conv: list[dict],
    msg_content: str,
    project: str,
    cfg: Any,
    cm: ConfigManager,
    client: Any,
    docs: list | None = None,
) -> dict[str, str]:
    # Pre-flight API key check
    if hasattr(client, "has_valid_api_key") and not client.has_valid_api_key():
        return {
            "content": (
                f"API key not configured for provider '{cfg.llm.provider}'. "
                "Set the environment variable or add the key in Model Config."
            ),
            "reasoning_content": "",
        }

    api_messages = _prepare_agent_context(conv, msg_content, project, cfg, cm, client, docs)

    max_rounds = 20
    max_repair_attempts = 2
    repair_count = 0
    tool_calls_used = 0
    consecutive_failures = 0
    for _ in range(max_rounds):
        try:
            resp = client.complete_with_tools(messages=api_messages, tools=TOOLS)
        except Exception as e:
            if _is_reasoning_handoff_error(e) and repair_count < max_repair_attempts:
                repair_count += 1
                resp, repaired, retry_error = _retry_after_reasoning_handoff_repair(client, api_messages)
                if resp is not None:
                    api_messages = repaired
                    continue
                # Exhausted repair attempts
                return {
                    "content": (
                        f"Error calling LLM after {max_repair_attempts} recovery attempts: {retry_error or e}. "
                        "Try resetting the conversation or checking the API configuration."
                    ),
                    "reasoning_content": "",
                }
            else:
                return {
                    "content": f"Error calling LLM: {e}",
                    "reasoning_content": "",
                }

        if resp.tool_calls:
            # Add assistant message with tool_calls BEFORE tool results (API requirement)
            tc_data = []
            for tc in resp.tool_calls:
                tc_data.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function_name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}
                })
            assistant_msg = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": resp.content or None,
                "tool_calls": tc_data,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if resp.reasoning_content:
                assistant_msg["reasoning_content"] = resp.reasoning_content
            api_messages.append(assistant_msg)
            conv.append(assistant_msg)
            for tc in resp.tool_calls:
                try:
                    result, tool_calls_used = await _execute_tool_with_limits(
                        tc.function_name,
                        tc.arguments,
                        cfg,
                        cm,
                        used_calls=tool_calls_used,
                    )
                except (AgentToolLimitError, AgentToolTimeoutError) as e:
                    return {
                        "content": str(e),
                        "reasoning_content": "",
                    }
                if _is_tool_result_failure(result):
                    consecutive_failures += 1
                    if consecutive_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
                        return {
                            "content": _build_consecutive_failure_limit_message(consecutive_failures),
                            "reasoning_content": "",
                        }
                else:
                    consecutive_failures = 0
                ok, summary = _format_tool_result_summary(tc.function_name, tc.arguments, result)
                _agent_log.info("%s → %s", tc.function_name, summary) if ok else _agent_log.warning("%s → %s", tc.function_name, summary)
                tool_content = _serialize_tool_content_for_llm(result)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_content,
                }
                api_messages.append(tool_msg)
                conv.append({
                    "id": str(uuid.uuid4()),
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_content,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
        else:
            return {
                "content": resp.content,
                "reasoning_content": resp.reasoning_content or "",
            }

    return {
        "content": _try_extract_skill(conv, project, cfg, cm, client) if conv[-1].get("role") == "tool" else
        "I've reached the maximum number of tool call rounds. Please ask me to continue if needed.",
        "reasoning_content": "",
    }


def _try_extract_skill(conv: list[dict], project: str, cfg: Any, cm: ConfigManager, client: Any) -> str:
    """After a successful tool workflow, try auto-extracting a reusable skill."""
    try:
        from luxar.core.skill_extractor import SkillExtractor
        # Build conversation text from last 10 messages for context
        conv_text = "\n".join(
            f"[{m.get('role','?')}]: {str(m.get('content',''))[:500]}"
            for m in conv[-10:]
        )
        # Try to find a workflow result
        workflow_result = {"success": True, "workflow": {"steps": [
            {"status": "completed"}, {"status": "completed"}, {"status": "completed"}
        ]}}
        # Check if conv has tool results resembling a workflow
        has_tool_calls = any(m.get("role") == "tool" for m in conv)
        if not has_tool_calls:
            return "I've reached the maximum number of tool call rounds."

        extractor = SkillExtractor(skill_library_root=cm.skill_library_root())
        data = extractor.extract(conv_text, workflow_result, client)
        if data:
            path = extractor.save_skill(data, project or "global")
            return f"Workflow completed. {chr(10)}📝 Auto-extracted skill: {data.get('device','')} ({data.get('protocol','')})"
    except Exception:
        pass
    return "I've reached the maximum number of tool call rounds. Please ask me to continue if needed."


async def _run_agent_loop_stream(
    conv: list[dict],
    msg_content: str,
    project: str,
    cfg: Any,
    cm: ConfigManager,
    client: Any,
    docs: list | None = None,
):
    # Pre-flight API key check to prevent confusing hangs downstream
    if hasattr(client, "has_valid_api_key") and not client.has_valid_api_key():
        yield {
            "event": "error",
            "data": json.dumps({
                "error": (
                    f"API key not configured for provider '{cfg.llm.provider}'. "
                    "Set the environment variable or add the key in Model Config."
                )
            }),
        }
        return

    api_messages = _prepare_agent_context(conv, msg_content, project, cfg, cm, client, docs)

    max_rounds = 20
    max_repair_attempts = 2
    repair_count = 0
    consecutive_failures = 0
    final_content = ""
    final_reasoning = ""
    tool_calls_used = 0
    for _ in range(max_rounds):
        round_content = ""
        round_reasoning = ""
        collected_args = ""
        collected_tc_id = ""
        collected_tc_name = ""
        try:
            for event in client.complete_stream(messages=api_messages, tools=TOOLS):
                if event["type"] == "token":
                    round_content += event.get("content", "")
                    round_reasoning += event.get("reasoning_content", "")
                    if event.get("content"):
                        yield {"event": "token", "data": json.dumps({"token": event["content"]})}
                elif event["type"] == "tool_call":
                    collected_tc_id = collected_tc_id or event["id"]
                    collected_tc_name = collected_tc_name or event["name"]
                    collected_args += event.get("arguments", "")
                    yield {"event": "tool_call", "data": json.dumps({"tool_call": event["name"]})}
        except Exception as e:
            if _is_reasoning_handoff_error(e) and repair_count < max_repair_attempts:
                repair_count += 1
                aggressive = repair_count > 1
                repaired = _repair_messages_for_reasoning_handoff(api_messages, aggressive=aggressive)
                if repaired != api_messages:
                    api_messages = repaired
                yield {"event": "reset_output", "data": json.dumps({"reason": "reasoning_handoff_retry"})}
                yield {
                    "event": "warning",
                    "data": json.dumps({
                        "warning": (
                            f"Recovered from stale reasoning context and retried "
                            f"(attempt {repair_count}/{max_repair_attempts})."
                        )
                    }),
                }
                continue
            yield {
                "event": "error",
                "data": json.dumps({
                    "error": str(e),
                    "detail": (
                        "The assistant encountered an error. If this persists, "
                        "try resetting the conversation or checking the API key configuration."
                    ),
                }),
            }
            return

        if collected_tc_name:
            try:
                args = json.loads(collected_args) if collected_args.strip() else {}
            except json.JSONDecodeError:
                args = {}
            # Add assistant message with tool_calls BEFORE tool result (API requirement)
            ast_msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": collected_tc_id,
                    "type": "function",
                    "function": {"name": collected_tc_name, "arguments": collected_args}
                }]
            }
            if round_reasoning:
                ast_msg["reasoning_content"] = round_reasoning
            ast_msg["id"] = str(uuid.uuid4())
            ast_msg["created_at"] = datetime.now(timezone.utc).isoformat()
            api_messages.append(ast_msg)
            conv.append(ast_msg)
            try:
                tool_calls_used = _enforce_tool_call_budget(collected_tc_name, tool_calls_used)
            except AgentToolLimitError as e:
                yield {"event": "error", "data": json.dumps({"error": str(e)})}
                return
            yield {
                "event": "tool_running",
                "data": json.dumps(_build_tool_running_payload(collected_tc_name, args), ensure_ascii=False),
            }
            yield {
                "event": "phase_changed",
                "data": json.dumps({"phase": "tool_running", "tool": collected_tc_name}, ensure_ascii=False),
            }
            try:
                result = await _execute_tool_with_timeout(collected_tc_name, args, cfg, cm)
            except AgentToolTimeoutError as e:
                yield {"event": "error", "data": json.dumps({"error": str(e)})}
                yield {
                    "event": "escalation_triggered",
                    "data": json.dumps({"reason": "tool_timeout", "tool": collected_tc_name}, ensure_ascii=False),
                }
                return
            if _is_tool_result_failure(result):
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": _build_consecutive_failure_limit_message(consecutive_failures)}),
                    }
                    yield {
                        "event": "escalation_triggered",
                        "data": json.dumps({"reason": "consecutive_tool_failures", "count": consecutive_failures}, ensure_ascii=False),
                    }
                    return
            else:
                consecutive_failures = 0
            tool_msg = {
                "role": "tool",
                "tool_call_id": collected_tc_id,
                "content": _serialize_tool_content_for_llm(result),
            }
            api_messages.append(tool_msg)
            conv.append({
                "id": str(uuid.uuid4()),
                "role": "tool",
                "tool_call_id": collected_tc_id,
                "content": _serialize_tool_content_for_llm(result),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            yield {
                "event": "tool_result",
                "data": json.dumps({"tool": collected_tc_name, "result": _serialize_tool_data(result.data)}, ensure_ascii=False),
            }
            if collected_tc_name in {"skills_list", "skill_view", "skill_execute"}:
                yield {
                    "event": "skill_loaded",
                    "data": json.dumps({"tool": collected_tc_name, "result": result.data}, ensure_ascii=False),
                }
            if collected_tc_name == "lesson_record":
                yield {
                    "event": "lesson_recorded",
                    "data": json.dumps(result.data, ensure_ascii=False),
                }
            if collected_tc_name in {"skill_promote", "lesson_promote"}:
                yield {
                    "event": "promotion_applied",
                    "data": json.dumps({"tool": collected_tc_name, "result": result.data}, ensure_ascii=False),
                }
            ok, summary = _format_tool_result_summary(collected_tc_name, args, result)
            _agent_log.info("%s → %s", collected_tc_name, summary) if ok else _agent_log.warning("%s → %s", collected_tc_name, summary)
            yield {
                "event": "log",
                "data": json.dumps({"success": ok, "message": summary, "tool": collected_tc_name}, ensure_ascii=False),
            }
        else:
            final_content = round_content
            final_reasoning = round_reasoning
            break
    else:
        final_content += "\n\n_I've reached the maximum number of tool call rounds. Please ask me to continue if needed._"

    final_message = {
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "content": final_content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if final_reasoning:
        final_message["reasoning_content"] = final_reasoning
    conv.append(final_message)
    _save_conv(project)
    yield {"event": "done", "data": "[DONE]"}


# ===== FastAPI Application Factory =====

def create_app(config_path: str | None = None) -> FastAPI:
    app = FastAPI(title="Luxar API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    cm = ConfigManager(config_path)
    cfg = cm.ensure_default_config()

    global _conv_store
    if _conv_store:
        try:
            _conv_store.close()
        except Exception:
            pass
    _conv_store = ConversationStore(cm.workspace_root())

    ui_dir = Path(__file__).resolve().parent.parent.parent.parent / "ui" / "public"
    if ui_dir.exists():
        app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    @app.get("/")
    def serve_index():
        index = ui_dir / "index.html" if ui_dir.exists() else None
        if index and index.exists():
            return FileResponse(str(index))
        return {"message": "Luxar API - visit /docs for Swagger UI"}

    @app.get("/api/config")
    def get_config():
        return cfg.model_dump(mode="json")

    @app.put("/api/config")
    async def update_config(body: dict):
        if "llm" in body:
            for k, v in body["llm"].items():
                if hasattr(cfg.llm, k):
                    setattr(cfg.llm, k, v)
        if "api_keys" in body and isinstance(body["api_keys"], dict):
            cfg.api_keys.update(body["api_keys"])
        from ruamel.yaml import YAML
        _yaml = YAML(typ="safe")
        config_path = cm.config_path
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open("w", encoding="utf-8") as f:
            _yaml.dump(cfg.model_dump(mode="json"), f)
        return {"status": "ok", "config": cfg.model_dump(mode="json")}

    @app.get("/api/conversations/{project}")
    def get_conversation(project: str):
        conv = _get_conv(project)
        return {"messages": conv, "project": project}

    @app.post("/api/conversations/{project}")
    async def send_message(project: str, body: dict):
        """Send a message to the agent. Set body.stream=true for SSE streaming response."""
        msg_content = body.get("message", "") or body.get("content", "")
        stream = body.get("stream", False)
        conv = _get_conv(project)
        normalized_project = _normalize_project_name(project)

        user_msg = {
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": msg_content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        conv.append(user_msg)
        docs = body.get("docs", []) or []
        project_payload = _parse_project_creation_request(msg_content) if not normalized_project and not docs else None
        if project_payload and project_payload.get("error"):
            assistant_msg = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": project_payload["error"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            conv.append(assistant_msg)
            _save_conv(project)
            if stream:
                async def _error_stream():
                    yield {"event": "token", "data": json.dumps({"token": project_payload["error"]})}
                    yield {"event": "done", "data": "[DONE]"}
                return EventSourceResponse(_error_stream())
            return {"message": assistant_msg, "project": project}

        if project_payload:
            if stream:
                return EventSourceResponse(
                    _stream_project_template_creation(conv, project_payload, cfg, cm, project)
                )
            try:
                created = _create_project_from_template(project_payload, cfg, cm)
            except Exception as exc:
                assistant_msg = {
                    "id": str(uuid.uuid4()),
                    "role": "assistant",
                    "content": str(exc),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                conv.append(assistant_msg)
                _save_conv(project)
                return {"message": assistant_msg, "project": project}
            summary = _build_project_creation_summary(project_payload, project_payload["name"].strip())
            assistant_msg = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": summary,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            conv.append(assistant_msg)
            _save_conv(project)
            return {
                "message": assistant_msg,
                "project": project,
                "created_project": created.model_dump(mode="json") if hasattr(created, "model_dump") else created,
            }

        from luxar.core.llm_client import LLMClient
        client = LLMClient(cfg)

        if stream:
            return EventSourceResponse(_run_agent_loop_stream(conv, msg_content, normalized_project, cfg, cm, client, docs))
        else:
            reply = await _run_agent_loop(conv, msg_content, normalized_project, cfg, cm, client, docs)
            assistant_msg = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": reply.get("content", ""),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            if reply.get("reasoning_content"):
                assistant_msg["reasoning_content"] = reply["reasoning_content"]
            conv.append(assistant_msg)
            _save_conv(project)
            return {"message": assistant_msg, "project": project}

    @app.post("/api/conversations/{project}/reset")
    def reset_conversation(project: str):
        _conv_cache.pop(project, None)
        if _conv_store:
            _conv_store.delete(project)
        return {"status": "ok", "project": project}

    @app.post("/api/conversations/{project}/import")
    def import_conversation(project: str, body: dict):
        source_project = (body.get("source_project", "") or "").strip()
        replace = bool(body.get("replace", True))
        if not source_project:
            raise HTTPException(status_code=400, detail="'source_project' is required.")

        source_conv = list(_get_conv(source_project))
        target_conv = [] if replace else list(_get_conv(project))
        copied = [dict(message) for message in source_conv]
        merged = copied if replace else target_conv + copied
        _conv_cache[project] = merged
        _save_conv(project)
        return {
            "status": "ok",
            "project": project,
            "source_project": source_project,
            "imported_messages": len(copied),
            "total_messages": len(merged),
        }

    @app.get("/api/projects")
    def list_projects():
        ws = cm.workspace_root()
        projects = []
        for meta_file in sorted(ws.glob("*/.agent_project.json")):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                projects.append(data)
            except Exception:
                projects.append({"name": meta_file.parent.name, "error": "invalid metadata"})
        return {"projects": projects}

    @app.post("/api/projects")
    async def create_project(body: dict):
        name = (body.get("name", "") or "").strip()
        mcu = (body.get("mcu", "") or "").strip()
        if not name or not mcu:
            raise HTTPException(status_code=400, detail="Both 'name' and 'mcu' are required.")
        project = run_init_project(
            workspace=str(cm.workspace_root()),
            name=name,
            mcu=mcu,
            platform=body.get("platform", cfg.platform.default_platform),
            runtime=body.get("runtime", cfg.platform.default_runtime),
            project_mode=body.get("project_mode", cfg.stm32.project_mode),
            firmware_package=body.get("firmware_package", cfg.stm32.firmware_package),
        )
        return {"project": project.model_dump(mode="json")}

    @app.post("/api/projects/import")
    async def import_project(body: dict):
        source_path = (body.get("source_path", "") or "").strip()
        if not source_path:
            raise HTTPException(status_code=400, detail="'source_path' is required.")
        manager = ProjectManager(str(cm.workspace_root()))
        project = manager.import_project(
            source_path=source_path,
            name=(body.get("name", "") or "").strip() or None,
            mcu=(body.get("mcu", "") or "").strip(),
            platform=body.get("platform", cfg.platform.default_platform),
            runtime=body.get("runtime", cfg.platform.default_runtime),
            project_mode=body.get("project_mode", cfg.stm32.project_mode),
            firmware_package=body.get("firmware_package", cfg.stm32.firmware_package),
        )
        return {"project": project.model_dump(mode="json")}

    @app.delete("/api/projects/{name}")
    def delete_project(name: str):
        import shutil
        ws = cm.workspace_root()
        project_dir = ws / name
        if not project_dir.exists():
            raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
        # Clean up conversation state
        _conv_cache.pop(name, None)
        if _conv_store:
            try:
                _conv_store.delete(name)
            except Exception:
                pass
        # Remove project directory
        try:
            shutil.rmtree(str(project_dir))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete project: {exc}")
        return {"status": "ok", "deleted": name}

    @app.get("/api/pick-directory")
    def pick_directory():
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory()
            root.destroy()
            return {"path": selected or ""}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Directory picker unavailable: {exc}") from exc

    @app.get("/api/pick-files")
    def pick_files():
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askopenfilenames(
                filetypes=[
                    ("Documents", "*.pdf *.md *.txt *.docx"),
                    ("PDF", "*.pdf"),
                    ("Markdown", "*.md"),
                    ("Text", "*.txt"),
                    ("Word", "*.docx"),
                    ("All files", "*.*"),
                ]
            )
            root.destroy()
            return {"paths": list(selected or [])}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"File picker unavailable: {exc}") from exc

    @app.get("/api/projects/{name}")
    def get_project(name: str):
        ws = cm.workspace_root()
        meta_file = ws / name / ".agent_project.json"
        if not meta_file.exists():
            raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        data["status"] = _project_status(ws / name)
        return data

    @app.post("/api/runtime/run")
    def api_runtime_run(body: dict):
        task = str(body.get("task", "") or body.get("message", "")).strip()
        return run_runtime(task=task, project=str(body.get("project", "")))

    @app.get("/api/runtime/explain")
    def api_runtime_explain():
        return explain_runtime_tool()

    @app.get("/api/memory")
    def api_memory(target: str = Query("memory")):
        return memory_read(target=target)

    @app.post("/api/memory")
    def api_memory_write(body: dict):
        return memory_write(
            content=str(body.get("content", "")),
            target=str(body.get("target", "memory")),
            append=bool(body.get("append", True)),
        )

    @app.get("/api/memory/lessons")
    def api_memory_lessons(query: str = Query(""), limit: int = Query(5)):
        return memory_lessons(query=query, limit=limit)

    @app.post("/api/memory/lessons")
    def api_memory_record_lesson(body: dict):
        return memory_lesson_record(payload=body, promoted=bool(body.get("promoted", False)))

    @app.post("/api/memory/lessons/promote")
    def api_memory_promote_lesson(body: dict):
        return memory_lesson_promote(
            slug=str(body.get("slug", "")),
            evidence_count=int(body.get("evidence_count", 1)),
        )

    @app.get("/api/session-search")
    def api_session_search(query: str = Query(...)):
        return memory_search(query=query)

    @app.get("/api/workspace")
    def api_workspace_inspect():
        return workspace_inspect()

    @app.post("/api/workspace/build")
    def api_workspace_build(body: dict):
        result = workspace_build(project=str(body.get("project", "")), clean=bool(body.get("clean", False)))
        return result.model_dump(mode="json") if hasattr(result, "model_dump") else result

    @app.post("/api/workspace/flash")
    def api_workspace_flash(body: dict):
        result = workspace_flash(project=str(body.get("project", "")), probe=str(body.get("probe", "")))
        return result.model_dump(mode="json") if hasattr(result, "model_dump") else result

    @app.post("/api/workspace/monitor")
    def api_workspace_monitor(body: dict):
        result = workspace_monitor(
            project=str(body.get("project", "")),
            port=str(body.get("port", "")),
            baudrate=int(body.get("baudrate", 115200)),
        )
        return result.model_dump(mode="json") if hasattr(result, "model_dump") else result

    @app.post("/api/workspace/probe")
    def api_workspace_probe(body: dict):
        return workspace_probe(project=str(body.get("project", "")), probe_type=str(body.get("probe_type", "i2c")))

    @app.get("/api/skills")
    def api_skills(category: str | None = Query(None)):
        return vnext_skills_list(category=category)

    @app.get("/api/skills/{name}")
    def api_skill_view(name: str):
        result = skill_view(name=name)
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        return result

    @app.post("/api/skills/manage")
    def api_skill_manage(body: dict):
        return skill_manage(
            action=str(body.get("action", "")),
            name=str(body.get("name", "")),
            category=str(body.get("category", "workflows")),
            content=str(body.get("content", "")),
            old_string=str(body.get("old_string", "")),
            new_string=str(body.get("new_string", "")),
        )

    @app.post("/api/skills/{name}/promote")
    def api_skill_promote(name: str, body: dict):
        return skill_promote(
            name=name,
            category=str(body.get("category", "")),
            promotion_level=str(body.get("promotion_level", "validated")),
        )

    @app.post("/api/skills/{name}/execute")
    def api_skill_execute(name: str, body: dict):
        return skill_execute(
            name=name,
            category=str(body.get("category", "")),
            project=str(body.get("project", "")),
            port=str(body.get("port", "")),
            baudrate=int(body.get("baudrate", 115200)),
        )

    @app.post("/api/analyze-docs")
    async def api_analyze_docs(body: dict):
        docs = body.get("docs", []) or []
        analyzer = DocumentEngineeringAnalyzer(cm.driver_library_root() / "knowledge_base")
        context = analyzer.analyze(docs=docs, query=body.get("query", ""))
        return {"engineering_context": context.model_dump(mode="json")}

    @app.get("/api/firmware-library")
    def get_firmware_library():
        fm = FirmwareLibraryManager(cm.firmware_library_root())
        pkgs = fm.list_packages()
        return {"packages": pkgs}

    @app.on_event("shutdown")
    def close_conversation_store():
        global _conv_store
        if _conv_store:
            try:
                _conv_store.close()
            except Exception:
                pass
            _conv_store = None


    return app


def _project_status(project_path: Path) -> dict:
    status: dict[str, Any] = {}
    git_dir = project_path / ".git"
    if git_dir.exists():
        try:
            gm = GitManager(str(project_path))
            status["git"] = {
                "branch": gm.repo.active_branch.name,
                "modified": len(gm.changed_files().get("modified", [])),
                "untracked": len(gm.changed_files().get("untracked", [])),
            }
        except Exception:
            status["git"] = {"error": "git failed"}
    build_dir = project_path / "build"
    status["has_build_dir"] = build_dir.exists()
    drivers_dir = project_path / "Drivers"
    status["has_drivers"] = drivers_dir.exists() and any(drivers_dir.iterdir())
    return status


def main():
    import uvicorn
    uvicorn.run("luxar.server.app:create_app", factory=True, host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()

