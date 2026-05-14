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
from luxar.core.driver_library import DriverLibrary
from luxar.core.knowledge_base import KnowledgeBase
from luxar.core.project_manager import ProjectManager
from luxar.core.skill_manager import SkillManager
from luxar.core.task_router import TaskRouter
from luxar.core.toolchain_manager import ToolchainManager
from luxar.core.firmware_library_manager import FirmwareLibraryManager
from luxar.core.git_manager import GitManager
from luxar.core.review_engine import ReviewEngine
from luxar.core.driver_generator import DriverGenerator
from luxar.core.driver_pipeline import DriverPipeline
from luxar.core.code_fixer import CodeFixer
from luxar.core.conversation_store import ConversationStore
from luxar.core.context_compressor import ContextCompressor, count_tokens
from luxar.core.llm_client import _OPENAI_PROVIDERS
from luxar.tools.run_task import run_task, run_task_stream
from luxar.tools.init_project import run_init_project
from luxar.models.schemas import DriverGenerationResult, WorkflowRunResult


# ===== Tool Definitions (OpenAI Function Calling schema) =====

MAX_AGENT_TOOL_CALLS = 20
MAX_AGENT_TOOL_TIMEOUT_SEC = 180
_TOOL_TIMEOUT_OVERRIDES: dict[str, int] = {
    "analyze_document_engineering": 300,  # pymupdf+RapidOCR fallback for scanned PDFs
}
PROJECT_LEVEL_RUN_TASK_INTENTS = {"forge_project", "debug_project"}


class AgentToolLimitError(RuntimeError):
    """Raised when the agent exceeds the allowed number of tool calls."""


class AgentToolTimeoutError(RuntimeError):
    """Raised when a single tool call exceeds the allowed runtime."""

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "run_task",
            "description": "Execute a complex multi-step embedded workflow (forge a project, run debug loop, generate a driver). Use only when the user explicitly requests a full project-level action involving multiple stages (plan, generate, review, fix, build). For single-step actions like build, flash, review, or git status, use their specific tools instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Natural-language task description"},
                    "project": {"type": "string", "description": "Optional project name"},
                    "docs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional document paths to analyze before routing the task",
                    },
                    "dry_run": {"type": "boolean", "description": "If true, plan without modifying files"},
                    "plan_only": {"type": "boolean", "description": "If true, return a structured execution plan only"},
                    "no_build": {"type": "boolean", "description": "Skip build stage"},
                    "no_flash": {"type": "boolean", "description": "Skip flash stage"},
                    "no_monitor": {"type": "boolean", "description": "Skip monitor stage"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_document_engineering",
            "description": "Extract structured engineering facts from one or more documents, including pins, buses, protocol frames, bring-up steps, timing constraints, and integration notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "docs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Document paths to analyze",
                    },
                    "query": {"type": "string", "description": "Optional query to focus extraction"},
                },
                "required": ["docs"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_context",
            "description": "Get a unified project context including project metadata, git summary, toolchains, and local assets relevant to planning and chat assistance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "init_project",
            "description": "Create a new empty STM32 project. Use 'stm32cubemx' for CubeMX-oriented projects, or 'stm32firmware' for bare firmware skeletons. Project creation does not generate a .ioc file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name, e.g. BlinkTest"},
                    "mcu": {"type": "string", "description": "MCU model, e.g. STM32F103C8T6 (default)"},
                    "platform": {"type": "string", "description": "Project type: stm32cubemx (CubeMX-oriented) or stm32firmware (bare skeleton)"},
                    "runtime": {"type": "string", "description": "baremetal or freertos (default: baremetal)"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_project",
            "description": "Build a project using CMake and Ninja. Optionally perform a clean build first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "clean": {"type": "boolean", "description": "Whether to clean build first"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flash_project",
            "description": "Flash the compiled firmware binary to the target MCU via ST-Link programmer or another probe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "probe": {"type": "string", "description": "Probe/debugger type, e.g. stlink"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "monitor_project",
            "description": "Open a serial (UART) monitor session to read device output from a given COM port.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "port": {"type": "string", "description": "Serial port, e.g. COM3"},
                    "baudrate": {"type": "integer", "description": "Baud rate, default 115200"},
                },
                "required": ["project", "port"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "debug_loop",
            "description": "Run the full build -> flash -> monitor debug loop with automatic recovery for build errors, flash failures, and monitor issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "probe": {"type": "string", "description": "Probe type, e.g. stlink"},
                    "port": {"type": "string", "description": "Serial port, e.g. COM3"},
                    "clean": {"type": "boolean", "description": "Clean build before starting"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_project",
            "description": "Run a multi-layer code review. When called without a file, reviews all App/ source files plus Core/ files that contain USER CODE sections (e.g. main.c, freertos.c). Pure CubeMX-generated Core/ files are skipped.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "file": {"type": "string", "description": "Optional specific file to review (e.g. App/Src/app_main.c). If omitted, reviews all source files."},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fix_code",
            "description": "Auto-fix code issues for a file and WRITE the changes back to disk unless dry_run=true. Editable files depend on project mode: in firmware mode, App/ files, Core/ skeleton files (main.c, system_stm32xx.c), CMakeLists.txt, and stm32f1xx_hal_conf.h are all editable; in CubeMX mode, only App/ files, CMakeLists.txt, stm32f1xx_hal_conf.h, and USER CODE sections in Core/ are editable. Drivers/ files are never editable. Accepts build_error for compilation errors that static review cannot detect.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "file": {"type": "string", "description": "File to fix, e.g. App/Src/app_main.c, Core/Src/main.c, CMakeLists.txt, or Core/Inc/stm32f1xx_hal_conf.h"},
                    "dry_run": {"type": "boolean", "description": "If true, show proposed fixes without modifying the file"},
                    "build_error": {"type": "string", "description": "Compilation error message from build_project.stderr for this specific file, if any"},
                },
                "required": ["project", "file"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show git diff since last human commit, list changed (modified/untracked) files, and show current branch for a project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                },
                "required": ["project"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": "List all initialized projects. Only use this in GLOBAL mode (no active project). When a project IS active, you already know its name.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toolchain_status",
            "description": "Show the status of all configured toolchains (cmake, arm-gcc, ninja, openocd, stm32 programmer CLI).",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_driver",
            "description": "Generate a new MCU-agnostic embedded driver (header + source) for a given chip and interface using the LLM. Optionally specify vendor and device for reuse context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chip": {"type": "string", "description": "Target chip, e.g. BMI270"},
                    "interface": {"type": "string", "description": "Communication interface, e.g. SPI, I2C"},
                    "doc_summary": {"type": "string", "description": "Documentation summary describing the device and its protocol"},
                    "vendor": {"type": "string", "description": "Vendor name, e.g. Bosch"},
                    "device": {"type": "string", "description": "Device name, e.g. BMI270"},
                },
                "required": ["chip", "interface"],
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

    if name == "analyze_document_engineering":
        pins = len(data.get("pin_requirements") or [])
        regs = len(data.get("register_hints") or [])
        buses = len(data.get("bus_requirements") or [])
        errors = data.get("parse_errors") or []
        parts = []
        if pins: parts.append(f"{pins}个引脚定义")
        if regs: parts.append(f"{regs}个寄存器")
        if buses: parts.append(f"{buses}条总线需求")
        msg = f"文档解析完成: {', '.join(parts)}" if parts else "文档解析完成"
        if errors:
            msg += f", {len(errors)}个文档解析出错"
        elif not parts:
            return False, "文档解析未提取到有效信息"
        return True, msg

    if name == "fix_code":
        fixed = data.get("files_fixed") or data.get("fixed_count")
        if isinstance(fixed, (int, float)):
            return True, f"已修复 {int(fixed)} 个文件"
        fixed_list = data.get("fixed_files") or []
        if isinstance(fixed_list, list) and fixed_list:
            return True, f"已修复 {len(fixed_list)} 个文件"
        if data.get("applied") is True:
            return True, "已修复 1 个文件"
        if is_ok:
            return True, "代码修复完成"
        error_msg = data.get("error") or data.get("message", "")
        return False, f"修复失败: {error_msg[:80]}"

    if name == "review_project":
        issues = data.get("issues")
        if isinstance(issues, list):
            n = len(issues)
            return (n == 0, f"审查通过, {n} 个问题" if n == 0 else f"审查发现 {n} 个问题")
        count = data.get("issue_count") or data.get("total_issues")
        if isinstance(count, (int, float)):
            n = int(count)
            return (n == 0, f"审查通过, {n} 个问题" if n == 0 else f"审查发现 {n} 个问题")
        return (is_ok, "审查完成" if is_ok else f"审查失败: {data.get('error','')}")

    if name == "build_project":
        if is_ok:
            return True, "构建成功"
        stderr = data.get("stderr") or ""
        if "error" in stderr.lower():
            errors = stderr.lower().count("error:")
            return False, f"构建失败: {errors} 个错误"
        error_msg = data.get("error") or data.get("message", "") or "编译出错"
        return False, f"构建失败: {str(error_msg)[:80]}"

    if name == "git_status":
        branch = data.get("branch") or ""
        changes = data.get("changes") or []
        if isinstance(changes, dict):
            change_count = len(changes.get("modified", []) or []) + len(changes.get("untracked", []) or [])
            return True, f"工作区干净 ({branch})" if change_count == 0 else f"{change_count} 个文件变更 ({branch})"
        if isinstance(changes, list):
            return True, f"工作区干净 ({branch})" if not changes else f"{len(changes)} 个文件变更 ({branch})"
        return True, "获取 Git 状态完成"

    if name == "run_task":
        if is_ok:
            return True, "工作流执行完成"
        error_msg = data.get("error") or data.get("message", "")
        return False, f"工作流失败: {str(error_msg)[:80]}"

    if name == "flash_project":
        return (is_ok, "烧录成功" if is_ok else f"烧录失败: {data.get('error','')}")

    if name == "monitor_project":
        return True, "串口监控已启动"

    if name == "init_project":
        proj = data.get("name") or args.get("name", "")
        if is_ok:
            return True, f"项目 '{proj}' 已创建" if proj else "项目已创建"
        return False, f"创建项目失败: {data.get('error','')}"

    if name == "list_projects":
        projs = data if isinstance(data, list) else data.get("projects", [])
        return True, f"共 {len(projs)} 个项目"

    if name == "toolchain_status":
        if is_ok:
            return True, "工具链就绪"
        missing = data.get("missing", [])
        if missing:
            return False, f"工具链缺少: {', '.join(missing[:3])}"
        return False, "工具链检查失败"

    if name == "debug_loop":
        return (is_ok, "调试完成" if is_ok else f"调试失败: {data.get('error','')}")

    if name == "project_context":
        project_data = data.get("project", {}) if isinstance(data, dict) else {}
        project_name = project_data.get("name", "") if isinstance(project_data, dict) else ""
        return True, f"项目状态正常 ({project_name})" if project_name else "项目状态正常"

    if name == "generate_driver":
        chip = data.get("chip") or ""
        if is_ok:
            return True, f"驱动生成完成 ({chip})" if chip else "驱动生成完成"
        return False, f"驱动生成失败: {data.get('error','')}"

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
        if name == "run_task":
            result = run_task(
                config=cfg,
                project_root=str(cm.project_root()),
                workspace_root=str(cm.workspace_root()),
                driver_library_root=str(cm.driver_library_root()),
                task=args.get("task", ""),
                project_name=args.get("project", ""),
                docs=args.get("docs", []) or [],
                dry_run=args.get("dry_run", False),
                plan_only=args.get("plan_only", False),
                no_build=args.get("no_build", False),
                no_flash=args.get("no_flash", False),
                no_monitor=args.get("no_monitor", False),
            )
            return _build_tool_envelope(name, result)

        if name == "analyze_document_engineering":
            analyzer = DocumentEngineeringAnalyzer(kb_root)
            context = analyzer.analyze(
                docs=args.get("docs", []) or [],
                query=args.get("query", ""),
            )
            return _build_tool_envelope(name, context.model_dump(mode="json"))

        if name == "init_project":
            platform = args.get("platform", "stm32cubemx") or "stm32cubemx"
            try:
                result = run_init_project(
                    workspace=str(ws),
                    name=args.get("name", ""),
                    mcu=args.get("mcu", "STM32F103C8T6"),
                    platform=platform,
                    runtime=args.get("runtime", "baremetal"),
                    project_mode="cubemx" if platform == "stm32cubemx" else "firmware",
                    firmware_package=args.get("firmware_package", "STM32Cube_FW_F1"),
                )
            except FileExistsError as e:
                return _build_tool_envelope(name, {"success": False, "error": str(e)}, error=str(e))
            return _build_tool_envelope(name, result)

        if name == "project_context":
            if not project_path or not project_path.exists():
                return _build_tool_envelope(name, {"error": f"Project '{project}' not found"}, error=f"Project '{project}' not found")
            pm = ProjectManager(str(ws))
            loaded = pm.load_project(project)
            gm = GitManager(str(project_path))
            sm = SkillManager(cfg, project_root=str(cm.project_root()))
            tm = ToolchainManager(cfg, project_root=str(cm.project_root()))
            return _build_tool_envelope(
                name,
                {
                    "project": loaded.model_dump(mode="json"),
                    "status": _project_status(project_path),
                    "git": {
                        "branch": gm.repo.active_branch.name,
                        "changes": gm.changed_files(),
                    },
                    "toolchains": tm.status(),
                    "skills": sm.list_skills(),
                },
            )

        if name == "list_projects":
            projs = []
            for meta_file in sorted(ws.glob("*/.agent_project.json")):
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    projs.append(data)
                except Exception:
                    projs.append({"name": meta_file.parent.name, "error": "invalid metadata"})
            return _build_tool_envelope(name, {"projects": projs})

        if name == "toolchain_status":
            tm = ToolchainManager(cfg, project_root=str(cm.project_root()))
            return _build_tool_envelope(name, tm.status())

        if name == "project_status":
            if not project_path or not project_path.exists():
                return _build_tool_envelope(name, {"error": f"Project '{project}' not found"}, error=f"Project '{project}' not found")
            return _build_tool_envelope(name, _project_status(project_path))

        if name == "project_files":
            if not project_path or not project_path.exists():
                return _build_tool_envelope(name, {"error": f"Project '{project}' not found"}, error=f"Project '{project}' not found")
            engine = ReviewEngine(str(project_path))
            files = engine.discover_project_files()
            return _build_tool_envelope(name, {"files": files})

        if name == "git_status":
            if not project_path or not project_path.exists():
                return _build_tool_envelope(name, {"error": f"Project '{project}' not found"}, error=f"Project '{project}' not found")
            gm = GitManager(str(project_path))
            return _build_tool_envelope(name, {
                "diff": gm.get_diff_since_last_human_commit(),
                "changes": gm.changed_files(),
                "branch": gm.repo.active_branch.name,
            })

        if name == "build_project":
            if not project_path:
                return _build_tool_envelope(name, {"error": "No project specified"}, error="No project specified")
            from luxar.tools.build_project import run_build_project
            result = run_build_project(
                project_path=str(project_path),
                config=cfg,
                project_root=str(cm.project_root()),
                clean=args.get("clean", False),
            )
            return _build_tool_envelope(name, result)

        if name == "flash_project":
            if not project_path:
                return _build_tool_envelope(name, {"error": "No project specified"}, error="No project specified")
            from luxar.tools.flash_project import run_flash_project
            result = run_flash_project(
                project_path=str(project_path),
                config=cfg,
                project_root=str(cm.project_root()),
                probe=args.get("probe"),
            )
            return _build_tool_envelope(name, result)

        if name == "monitor_project":
            if not project_path:
                return _build_tool_envelope(name, {"error": "No project specified"}, error="No project specified")
            from luxar.tools.monitor_project import run_monitor_project
            result = run_monitor_project(
                project_path=str(project_path),
                port=args.get("port", ""),
                baudrate=args.get("baudrate", 115200),
            )
            return _build_tool_envelope(name, result)

        if name == "debug_loop":
            if not project_path:
                return _build_tool_envelope(name, {"error": "No project specified"}, error="No project specified")
            from luxar.tools.debug_loop_project import run_debug_loop_project
            result = run_debug_loop_project(
                project_path=str(project_path),
                config=cfg,
                project_root=str(cm.project_root()),
                probe=args.get("probe"),
                port=args.get("port", ""),
                clean=args.get("clean", False),
            )
            return _build_tool_envelope(name, result)

        if name == "review_project":
            if not project_path or not project_path.exists():
                return _build_tool_envelope(name, {"error": f"Project '{project}' not found"}, error=f"Project '{project}' not found")
            engine = ReviewEngine(str(project_path))
            file = args.get("file", "")
            if file:
                report = engine.review_file(str(project_path / file))
            else:
                report = engine.review_project()
            return _build_tool_envelope(name, report.model_dump(mode="json") if hasattr(report, "model_dump") else {"report": str(report)})

        if name == "fix_code":
            if not project_path:
                return _build_tool_envelope(name, {"error": "No project specified"}, error="No project specified")
            file = args.get("file", "")
            if not file:
                return _build_tool_envelope(name, {"error": "No file specified"}, error="No file specified")
            # build-project-level files that fix_code should always be able to edit:
            #   - CMakeLists.txt (any location) — build configuration
            #   - stm32f1xx_hal_conf.h / stm32_hal_conf.h — HAL project config
            always_editable = {"cmakelists.txt", "stm32f1xx_hal_conf.h", "stm32_hal_conf.h"}
            path_lower = str(Path(file).name).lower()

            # Determine project mode to adjust Core/ protection scope.
            # In "cubemx" mode, Core/ files are CubeMX-generated → protected.
            # In "firmware" mode, Core/ files are Luxar skeleton → editable.
            project_mode = "cubemx"
            try:
                meta = json.loads((project_path / ".agent_project.json").read_text(encoding="utf-8"))
                project_mode = str(meta.get("project_mode", "cubemx")).lower()
            except Exception:
                pass

            is_core_path = "core" in str(Path(file).parts).lower()
            is_always_editable = path_lower in always_editable

            if is_always_editable:
                pass
            elif project_mode == "cubemx":
                content = ""
                try:
                    content = (project_path / file).read_text(encoding="utf-8")
                except Exception:
                    pass
                if is_core_path and "USER CODE BEGIN" not in content:
                    message = f"Cannot auto-fix CubeMX-generated file '{file}'. Only App/ files, Core/ files with USER CODE sections, and project config files (CMakeLists.txt, stm32f1xx_hal_conf.h) are editable."
                    return _build_tool_envelope(name, {"error": message}, error=message)
            else:
                is_driver_path = "drivers" in str(Path(file).parts).lower()
                if is_driver_path:
                    message = f"Cannot auto-fix vendor driver file '{file}'. Driver files from STM32Cube firmware packages should not be modified."
                    return _build_tool_envelope(name, {"error": message}, error=message)
            fixer = CodeFixer(cfg)
            build_errors_list = None
            raw_build_error = args.get("build_error", "")
            if raw_build_error:
                build_errors_list = [raw_build_error]
            result = fixer.fix_file(
                project_path=str(project_path),
                file_path=str(project_path / file),
                build_errors=build_errors_list,
                apply_changes=not args.get("dry_run", False),
            )
            return _build_tool_envelope(name, result)

        if name == "generate_driver":
            from luxar.core.driver_generator import DriverGenerator
            gen = DriverGenerator(cfg, project_root=str(cm.project_root()))
            result = gen.generate_driver(
                chip=args.get("chip", ""),
                interface=args.get("interface", ""),
                protocol_summary=args.get("doc_summary", ""),
                register_summary=args.get("register_summary", ""),
                output_dir=str(cm.project_root() / "generated"),
                vendor=args.get("vendor", ""),
                device=args.get("device", ""),
            )
            return _build_tool_envelope(name, result)

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


async def _execute_run_task_stream(
    args: dict,
    cfg: Any,
    cm: ConfigManager,
):
    for event in run_task_stream(
        config=cfg,
        project_root=str(cm.project_root()),
        workspace_root=str(cm.workspace_root()),
        driver_library_root=str(cm.driver_library_root()),
        task=args.get("task", ""),
        project_name=args.get("project", ""),
        docs=args.get("docs", []) or [],
        dry_run=args.get("dry_run", False),
        plan_only=args.get("plan_only", False),
        no_build=args.get("no_build", False),
        no_flash=args.get("no_flash", False),
        no_monitor=args.get("no_monitor", False),
    ):
        yield event
        await asyncio.sleep(0)


def _classify_run_task_intent(args: dict) -> str:
    plan = TaskRouter().route(
        task=args.get("task", ""),
        project=args.get("project", ""),
        docs=args.get("docs", []) or [],
        dry_run=args.get("dry_run", False),
        plan_only=args.get("plan_only", False),
    )
    return plan.intent.intent_type


def _build_tool_running_payload(name: str, args: dict) -> dict:
    payload = {"tool": name}
    if name == "run_task":
        task = args.get("task", "")
        project = args.get("project", "")
        if task:
            payload["task"] = task
        if project:
            payload["project"] = project
    return payload


def _should_block_run_task_reentry(
    state: dict | None,
    args: dict,
) -> tuple[bool, str]:
    current_intent = _classify_run_task_intent(args)
    if not state or not state.get("seen"):
        return False, current_intent
    current_project = args.get("project", "")
    if current_project != state.get("project"):
        return False, current_intent
    status = state.get("status", "")
    if status in {"failed", "missing_info", "no_final"} and not state.get("recovery_used", False):
        return False, current_intent
    return True, current_intent


def _build_run_task_reentry_result(state: dict, args: dict) -> dict:
    prior_intent = state.get("intent", "forge_project")
    if prior_intent == "explain":
        message = (
            "run_task already produced an explanation in this turn. "
            "Do not call run_task again for the same project; continue with lightweight tools "
            "like project_context or summarize the blocker instead."
        )
        reason = "run_task_explain_reentry_blocked"
    else:
        message = (
            "A project-level workflow is already active in this turn. "
            "Do not call run_task again; continue from the existing workflow "
            "or use lightweight tools like project_context, build_project, or review_project."
        )
        reason = "run_task_reentry_blocked"
    return {
        "success": False,
        "mode": "execute",
        "intent": prior_intent,
        "message": message,
        "blocked": True,
        "reason": reason,
        "project": args.get("project", state.get("project", "")),
        "task": args.get("task", ""),
    }


def _is_active_project_context(project: str) -> bool:
    return bool(_normalize_project_name(project))


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


def _is_lightweight_project_inspection_task(task: str) -> bool:
    text = str(task or "").strip().lower()
    if not text:
        return False

    implementation_terms = (
        "实现", "生成", "创建", "新建", "添加", "修改", "修复", "烧录", "监视",
        "build", "flash", "monitor", "implement", "generate", "create", "add", "fix",
    )
    if any(term in text for term in implementation_terms):
        return False

    file_terms = (
        "文件", "文件列表", "目录", "目录结构", "结构", "状态",
        "file", "files", "tree", "structure", "status",
    )
    inspect_terms = (
        "查看", "看看", "列出", "显示", "当前", "状态",
        "show", "list", "check", "inspect", "current",
    )
    return any(term in text for term in file_terms) and any(term in text for term in inspect_terms)


def _should_block_lightweight_run_task(project: str, args: dict) -> bool:
    return _is_active_project_context(project) and _is_lightweight_project_inspection_task(args.get("task", ""))


def _build_lightweight_run_task_block_result(project: str, args: dict) -> dict:
    active_project = _normalize_project_name(project)
    return {
        "success": True,
        "blocked": True,
        "reason": "run_task_lightweight_query_blocked",
        "project": active_project,
        "task": args.get("task", ""),
        "message": (
            f"当前请求只是查看项目 '{active_project}' 的状态、文件或目录结构，"
            "不应启动 forge/run_task 工作流。请改用 project_context、project_files 或 git_status。"
        ),
    }


def _guard_run_task_call(project: str, state: dict | None, args: dict) -> tuple[dict | None, str]:
    current_intent = _classify_run_task_intent(args)
    if state and state.get("seen"):
        blocked, current_intent = _should_block_run_task_reentry(state, args)
        if blocked:
            return _build_run_task_reentry_result(state, args), current_intent
    if _should_block_lightweight_run_task(project, args):
        return _build_lightweight_run_task_block_result(project, args), current_intent
    return None, current_intent


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
You are Luxar, an embedded AI engineering assistant. You are currently working on STM32 project '{project}'.

## Current Project: {project}
- The user has already selected {project} as the active project.
- ALL project-specific actions (review, build, flash, status, files, git, etc.) should use "{project}" without asking.
- NEVER call list_projects — you are already in {project}.
- NEVER call init_project while inside this active project conversation. The project already exists.
- When the user says "审查" (review) → directly call review_project.
- When the user says "构建" (build) → directly call build_project.
- When the user says "状态" (status) → call project_context (it includes status, git, and files).
- When the user says "查看文件", "文件列表", "目录结构", or asks to inspect current files → call project_context or project_files. Do NOT call run_task/forge.
- Do NOT call multiple exploratory tools before the one the user asked for. Just call the right tool directly.
- For a full project task, you may call run_task ONCE to start the workflow.
- After run_task starts, continue with lightweight tools (fix_code, build_project, review_project). NEVER call run_task again.
- run_task is for full project-level implementation workflows inside the active project. It must not be used for status, files, git, review-only, build-only, flash-only, or monitor-only requests.
- fix_code accepts a build_error parameter — pass gcc error lines from build_project.stderr to fix errors that static review cannot see.

## CubeMX Rules (CRITICAL)
- These rules apply to CubeMX-mode projects ONLY.
- Core/ files (main.c, freertos.c, stm32*.c, system_*.c, syscalls.c, sysmem.c, *_hal_msp.c) are GENERATED by CubeMX.
- You may ONLY edit code inside existing /* USER CODE BEGIN ... */ ... /* USER CODE END */ blocks.
- NEVER create new USER CODE sections in Core/ files — this will break the CubeMX workflow.
- If the user asks you to edit Core/ files outside USER CODE blocks, explain that it will be overwritten by CubeMX regeneration and refuse.

## Firmware-Mode Rules (applies when project_mode=firmware)
- In firmware mode, Core/ files (main.c, system_stm32xx.c, startup_stm32.s) are Luxar-generated skeletons — they ARE editable.
- Core/Inc/stm32f1xx_hal_conf.h is a project config header — editable.
- CMakeLists.txt is a project build config — editable.
- App/ files (App/Src/*, App/Inc/*) are fully editable.
- Drivers/ files are STM32Cube firmware package files — DO NOT edit them.
- If a build error originates from an App/ or Core/ file, use fix_code immediately — do NOT hesitate.

## Common Editable Files (BOTH modes)
- CMakeLists.txt — always editable. Fix glob patterns, add/remove sources, etc.
- Core/Inc/stm32f1xx_hal_conf.h — always editable. Enable/disable HAL modules.
- App/Src/app_main.c, App/Inc/app_main.h — always editable.

## If you say "审查", call ONLY review_project. Do NOT call project_context/project_status/project_files first.

## Fix/Build Loop (IMPORTANT)
- When build_project fails with compilation errors: extract the file path and error message from stderr, then call fix_code with the specific file and build_error.
- Example: build_project fails with "App/Src/app_main.c:21:10: fatal error: stm32f10x.h: No such file" → call fix_code(project="manualtest", file="App/Src/app_main.c", build_error="App/Src/app_main.c:21:10: fatal error: stm32f10x.h: No such file")
- This fix→build loop resolves errors faster than re-running run_task.

## Review Results Interpretation
- When review_project reports issues in Core/ files (main.c, syscalls.c, system_*.c, etc.), IGNORE them. These are CubeMX/HAL generated files and cannot be modified.
- Only fix issues in App/ files. Core/ file issues are false positives for our workflow.
- If build fails due to review errors, check if the errors are in App/ or Core/. Only App/ errors need fixing.

## Language
- Respond in the same language the user uses. Chinese in -> Chinese out.

## Conversation
- Chat naturally. For casual conversation — respond directly.

## Tool usage
- You have tools for build, flash, review, forge, debug loop, git, etc.
- Call a tool only when the user explicitly asks for an action.
- Summarize tool results in natural language.
- Be concise.

## Tool Usage Rules (CRITICAL)
- ONLY call analyze_document_engineering when the user has explicitly provided document paths. For simple requests like "Blink LED", "Colorful LED", or "GPIO control", do NOT call document analysis — these do not require datasheets.
- If build_project fails, look at stderr for file paths and gcc error messages. Call fix_code with the specific file and build_error param containing the gcc error line. Do NOT call run_task for build failures.
- For small, explicit, low-risk App/ fixes, act automatically instead of asking for permission. Examples: replacing `printf` with HAL/UART output, adding missing Doxygen comments, fixing include mistakes, and other review-driven cleanup in App/ files.
- If you already know the exact fix from review/build output, do the fix immediately. Do NOT reply with “I can fix this if you want” or ask the user to confirm routine App/ code cleanup.
- After applying an automatic App/ fix, re-run review_project. If the user asked to build or the previous step was blocked by the review gate, re-run build_project once after the fix.
- Only stop and ask the user when the change is non-trivial, spans multiple possible designs, touches generated Core/ code outside USER CODE blocks, or may change behavior beyond the reported defect.
- If 2 consecutive tools fail with errors, STOP and report the error to the user. Do NOT blindly try more tools in a loop. Ask the user for clarification or next steps.
- Do NOT call multiple exploratory tools before the one the user asked for. Just call the right tool directly."""

GLOBAL_SYSTEM_PROMPT = """\
You are Luxar, a general embedded AI engineering assistant specialized in STM32 development.
You help users with embedded development concepts, code review, driver generation,
project planning, build, flash, monitor, debug, and git operations.

## Language
- Respond in the same language the user uses. Chinese in → Chinese out. English in → English out.

## Conversation
- Be a helpful conversational assistant first. Chat naturally, answer questions, explain concepts, give advice.
- For casual conversation, greetings, questions about your capabilities, or discussion about code — respond directly without calling any tool.

## Tool usage
- You have tools that can create projects (run_task/forge_project), list projects, check project status, etc.
- When the user asks to work on a specific project, use its name with tools.
- If no project is specified and the user asks about existing projects, use `list_projects` to see what is available.
- Only call tools when the user explicitly asks for a concrete action.
- For a full project-level task, call run_task only once to start the workflow.
- After run_task starts, do not call run_task again in the same turn. Continue from the workflow or use lightweight tools like project_context, build_project, or review_project.
- Do NOT call tools for casual conversation or questions.
- For routine, localized code cleanup that directly resolves a reported review/build issue, prefer doing the fix automatically rather than asking for confirmation.
- After a tool executes, summarize the result in natural language for the user.
- Be concise and helpful."""


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
    run_task_state = {
        "seen": False,
        "project": "",
        "task": "",
        "intent": "",
        "workflow_started": False,
        "status": "",
        "recovery_used": False,
    }
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
                if tc.function_name == "run_task":
                    guard_result, current_intent = _guard_run_task_call(project, run_task_state, tc.arguments)
                    if guard_result:
                        try:
                            result = _build_tool_envelope("run_task", guard_result)
                            tool_calls_used = _enforce_tool_call_budget("run_task", tool_calls_used)
                        except AgentToolLimitError as e:
                            return {
                                "content": str(e),
                                "reasoning_content": "",
                            }
                    else:
                        if run_task_state["seen"] and run_task_state["status"] in {"failed", "missing_info", "no_final"}:
                            run_task_state["recovery_used"] = True
                        run_task_state.update({
                            "seen": True,
                            "project": tc.arguments.get("project", ""),
                            "task": tc.arguments.get("task", ""),
                            "intent": current_intent,
                            "workflow_started": False,
                            "status": "started",
                        })
                        try:
                            result, tool_calls_used = await _execute_tool_with_limits(
                                tc.function_name,
                                tc.arguments,
                                cfg,
                                cm,
                                used_calls=tool_calls_used,
                            )
                            data = result.data if isinstance(result.data, dict) else {}
                            if isinstance(data, dict):
                                run_task_state["status"] = "completed" if data.get("success") else "failed"
                                if data.get("mode") == "plan" and not data.get("success"):
                                    run_task_state["status"] = "missing_info"
                        except (AgentToolLimitError, AgentToolTimeoutError) as e:
                            return {
                                "content": str(e),
                                "reasoning_content": "",
                            }
                elif tc.function_name == "init_project" and _is_active_project_context(project):
                    try:
                        result = _build_tool_envelope("init_project", _build_active_project_init_block_result(project, tc.arguments))
                        tool_calls_used = _enforce_tool_call_budget("init_project", tool_calls_used)
                    except AgentToolLimitError as e:
                        return {
                            "content": str(e),
                            "reasoning_content": "",
                        }
                else:
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
    run_task_state = {
        "seen": False,
        "project": "",
        "task": "",
        "intent": "",
        "workflow_started": False,
        "status": "",
        "recovery_used": False,
    }
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
            try:
                if collected_tc_name == "run_task":
                    guard_result, current_intent = _guard_run_task_call(project, run_task_state, args)
                    if guard_result:
                        result = _build_tool_envelope("run_task", guard_result)
                    else:
                        if run_task_state["seen"] and run_task_state["status"] in {"failed", "missing_info", "no_final"}:
                            run_task_state["recovery_used"] = True
                        run_task_state.update({
                            "seen": True,
                            "project": args.get("project", ""),
                            "task": args.get("task", ""),
                            "intent": current_intent,
                            "workflow_started": False,
                            "status": "started",
                        })
                        stream_runner = _execute_run_task_stream(args, cfg, cm)
                        final_result = None
                        try:
                            while True:
                                workflow_event = await stream_runner.__anext__()
                                if workflow_event.get("type") == "workflow_started":
                                    run_task_state["workflow_started"] = True
                                if workflow_event.get("type") in {"workflow_finished", "workflow_failed"}:
                                    payload = workflow_event.get("payload", {}) or {}
                                    if isinstance(payload.get("result"), dict):
                                        final_result = payload["result"]
                                yield {
                                    "event": workflow_event.get("type", "workflow_warning"),
                                    "data": json.dumps({
                                        key: value for key, value in workflow_event.items() if key != "type"
                                    }, ensure_ascii=False),
                                }
                        except StopAsyncIteration:
                            final_result = final_result or {
                                "success": False,
                                "mode": "execute",
                                "message": "run_task stream ended without a final result.",
                            }
                            run_task_state["status"] = "completed" if final_result.get("success") else "failed"
                            if final_result.get("mode") == "plan" and not final_result.get("success"):
                                run_task_state["status"] = "missing_info"
                            if final_result.get("message") == "run_task stream ended without a final result.":
                                run_task_state["status"] = "no_final"
                            result = _build_tool_envelope("run_task", final_result)
                elif collected_tc_name == "init_project" and _is_active_project_context(project):
                    result = _build_tool_envelope("init_project", _build_active_project_init_block_result(project, args))
                else:
                    result = await _execute_tool_with_timeout(collected_tc_name, args, cfg, cm)
            except AgentToolTimeoutError as e:
                yield {"event": "error", "data": json.dumps({"error": str(e)})}
                return
            if _is_tool_result_failure(result):
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_TOOL_FAILURES:
                    yield {
                        "event": "error",
                        "data": json.dumps({"error": _build_consecutive_failure_limit_message(consecutive_failures)}),
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

    @app.get("/api/toolchains")
    def get_toolchains():
        tm = ToolchainManager(cfg, project_root=str(cm.project_root()))
        return tm.status()

    @app.get("/api/skills")
    def list_skills(protocol: str | None = Query(None)):
        sm = SkillManager(cfg, project_root=str(cm.project_root()))
        return {"skills": sm.list_skills(protocol=protocol)}

    @app.get("/api/drivers")
    def search_drivers(
        keyword: str = Query(""),
        protocol: str | None = Query(None),
        vendor: str | None = Query(None),
        limit: int = Query(20),
    ):
        dl = DriverLibrary(str(cm.driver_library_root()))
        results = dl.search_drivers(keyword=keyword, protocol=protocol or "", vendor=vendor or "", limit=limit)
        return {"drivers": results}

    @app.get("/api/knowledge-base")
    def search_knowledge_base(query: str = Query(""), limit: int = Query(10)):
        kb_root = cm.driver_library_root() / "knowledge_base"
        if not query.strip():
            kb = KnowledgeBase(str(kb_root))
            stats = kb.stats()
            return {"stats": stats}
        kb = KnowledgeBase(str(kb_root))
        results = kb.search(query=query, limit=limit)
        return {"results": results}

    @app.post("/api/run-task")
    async def api_run_task(body: dict):
        return run_task(
            config=cfg,
            project_root=str(cm.project_root()),
            workspace_root=str(cm.workspace_root()),
            driver_library_root=str(cm.driver_library_root()),
            task=body.get("task", "") or body.get("message", ""),
            project_name=body.get("project", ""),
            docs=body.get("docs", []) or [],
            dry_run=body.get("dry_run", False),
            plan_only=body.get("plan_only", False),
            no_build=body.get("no_build", False),
            no_flash=body.get("no_flash", False),
            no_monitor=body.get("no_monitor", False),
        )

    @app.post("/api/analyze-docs")
    async def api_analyze_docs(body: dict):
        docs = body.get("docs", []) or []
        analyzer = DocumentEngineeringAnalyzer(cm.driver_library_root() / "knowledge_base")
        context = analyzer.analyze(docs=docs, query=body.get("query", ""))
        return {"engineering_context": context.model_dump(mode="json")}

    @app.get("/api/project-context/{name}")
    def get_project_context(name: str):
        ws = cm.workspace_root()
        project_path = ws / name
        if not project_path.exists():
            raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
        project = ProjectManager(str(ws)).load_project(name)
        gm = GitManager(str(project_path))
        tm = ToolchainManager(cfg, project_root=str(cm.project_root()))
        sm = SkillManager(cfg, project_root=str(cm.project_root()))
        return {
            "project": project.model_dump(mode="json"),
            "status": _project_status(project_path),
            "git": {
                "branch": gm.repo.active_branch.name,
                "changes": gm.changed_files(),
                "diff": gm.get_diff_since_last_human_commit(),
            },
            "toolchains": tm.status(),
            "skills": sm.list_skills(),
        }

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

    @app.get("/api/git/{project}")
    def get_git_status(project: str):
        ws = cm.workspace_root()
        meta_file = ws / project / ".agent_project.json"
        if not meta_file.exists():
            raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
        gm = GitManager(str(ws / project))
        return {
            "diff": gm.get_diff_since_last_human_commit(),
            "changes": gm.changed_files(),
            "branch": gm.repo.active_branch.name,
        }

    @app.post("/api/review/{project}")
    def review_project(project: str, file: str | None = Query(None)):
        ws = cm.workspace_root()
        project_path = ws / project
        if not project_path.exists():
            raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
        engine = ReviewEngine(str(project_path))
        if file:
            report = engine.review_file(str(project_path / file))
        else:
            report = engine.review_project()
        return {"report": report.model_dump(mode="json")}

    @app.post("/api/generate-driver")
    def generate_driver(
        chip: str = Query(...),
        interface: str = Query(...),
        doc_summary: str = Query(""),
        register_summary: str = Query(""),
        vendor: str = Query(""),
        device: str = Query(""),
        output_dir: str = Query(""),
    ):
        resolved_output = output_dir or str(cm.project_root() / "generated")
        generator = DriverGenerator(cfg, project_root=str(cm.project_root()))
        result = generator.generate_driver(
            chip=chip,
            interface=interface,
            protocol_summary=doc_summary,
            register_summary=register_summary,
            output_dir=resolved_output,
            vendor=vendor,
            device=device,
        )
        return result.model_dump(mode="json")

    @app.post("/api/generate-driver-loop")
    async def generate_driver_loop(
        chip: str = Query(...),
        interface: str = Query(...),
        doc_summary: str = Query(""),
        register_summary: str = Query(""),
        vendor: str = Query(""),
        device: str = Query(""),
        output_dir: str = Query(""),
        max_fix_iterations: int = Query(3),
    ):
        resolved_output = output_dir or str(cm.project_root() / "generated")

        async def event_generator():
            pipeline = DriverPipeline(cfg, project_root=str(cm.project_root()))
            yield {"event": "log", "data": json.dumps({"message": "Starting driver pipeline...", "phase": "init"})}

            def callback(phase: str, data: dict[str, Any]):
                return None

            result = pipeline.generate_review_fix(
                chip=chip,
                interface=interface,
                protocol_summary=doc_summary,
                register_summary=register_summary,
                output_dir=resolved_output,
                vendor=vendor,
                device=device,
                max_fix_iterations=max_fix_iterations,
                progress_callback=callback,
            )

            yield {
                "event": "result",
                "data": json.dumps(result.model_dump(mode="json") if hasattr(result, "model_dump") else result),
            }

        return EventSourceResponse(event_generator())

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

