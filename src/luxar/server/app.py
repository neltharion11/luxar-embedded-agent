from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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
from luxar.tools.run_task import run_task
from luxar.tools.init_project import run_init_project
from luxar.models.schemas import DriverGenerationResult, WorkflowRunResult


# ===== Tool Definitions (OpenAI Function Calling schema) =====

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
            "description": "Auto-fix code issues for a file. Can dry-run. Only works on App/ files and inside existing USER CODE sections of Core/ files. NEVER create new USER CODE sections. CubeMX-generated code outside USER CODE blocks cannot be touched.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Project name"},
                    "file": {"type": "string", "description": "File to fix, e.g. App/Src/app_main.c"},
                    "dry_run": {"type": "boolean", "description": "If true, show proposed fixes without modifying the file"},
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


def _execute_tool(name: str, args: dict, cfg: Any, cm: ConfigManager) -> str:
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
            return json.dumps(result, ensure_ascii=False)

        if name == "analyze_document_engineering":
            analyzer = DocumentEngineeringAnalyzer(kb_root)
            context = analyzer.analyze(
                docs=args.get("docs", []) or [],
                query=args.get("query", ""),
            )
            return json.dumps(context.model_dump(mode="json"), ensure_ascii=False)

        if name == "init_project":
            platform = args.get("platform", "stm32cubemx") or "stm32cubemx"
            result = run_init_project(
                workspace=str(ws),
                name=args.get("name", ""),
                mcu=args.get("mcu", "STM32F103C8T6"),
                platform="stm32cubemx",
                runtime=args.get("runtime", "baremetal"),
                project_mode="cubemx" if platform == "stm32cubemx" else "firmware",
                firmware_package=args.get("firmware_package", "STM32Cube_FW_F1"),
            )
            return json.dumps(result.model_dump(mode="json") if hasattr(result, "model_dump") else result, ensure_ascii=False)

        if name == "project_context":
            if not project_path or not project_path.exists():
                return json.dumps({"error": f"Project '{project}' not found"})
            pm = ProjectManager(str(ws))
            loaded = pm.load_project(project)
            gm = GitManager(str(project_path))
            sm = SkillManager(cfg, project_root=str(cm.project_root()))
            tm = ToolchainManager(cfg, project_root=str(cm.project_root()))
            return json.dumps(
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
                ensure_ascii=False,
            )

        if name == "list_projects":
            projs = []
            for meta_file in sorted(ws.glob("*/.agent_project.json")):
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    projs.append(data)
                except Exception:
                    projs.append({"name": meta_file.parent.name, "error": "invalid metadata"})
            return json.dumps({"projects": projs}, ensure_ascii=False)

        if name == "toolchain_status":
            tm = ToolchainManager(cfg, project_root=str(cm.project_root()))
            return json.dumps(tm.status(), ensure_ascii=False)

        if name == "project_status":
            if not project_path or not project_path.exists():
                return json.dumps({"error": f"Project '{project}' not found"})
            return json.dumps(_project_status(project_path), ensure_ascii=False)

        if name == "project_files":
            if not project_path or not project_path.exists():
                return json.dumps({"error": f"Project '{project}' not found"})
            engine = ReviewEngine(str(project_path))
            files = engine.discover_project_files()
            return json.dumps({"files": files}, ensure_ascii=False)

        if name == "git_status":
            if not project_path or not project_path.exists():
                return json.dumps({"error": f"Project '{project}' not found"})
            gm = GitManager(str(project_path))
            return json.dumps({
                "diff": gm.get_diff_since_last_human_commit(),
                "changes": gm.changed_files(),
                "branch": gm.repo.active_branch.name,
            }, ensure_ascii=False)

        if name == "build_project":
            if not project_path:
                return json.dumps({"error": "No project specified"})
            from luxar.tools.build_project import run_build_project
            result = run_build_project(
                project_path=str(project_path),
                config=cfg,
                project_root=str(cm.project_root()),
                clean=args.get("clean", False),
            )
            return json.dumps({"result": str(result)}, ensure_ascii=False)

        if name == "flash_project":
            if not project_path:
                return json.dumps({"error": "No project specified"})
            from luxar.tools.flash_project import run_flash_project
            result = run_flash_project(
                project_path=str(project_path),
                config=cfg,
                project_root=str(cm.project_root()),
                probe=args.get("probe"),
            )
            return json.dumps({"result": str(result)}, ensure_ascii=False)

        if name == "monitor_project":
            if not project_path:
                return json.dumps({"error": "No project specified"})
            from luxar.tools.monitor_project import run_monitor_project
            result = run_monitor_project(
                project_path=str(project_path),
                port=args.get("port", ""),
                baudrate=args.get("baudrate", 115200),
            )
            return json.dumps({"result": str(result)}, ensure_ascii=False)

        if name == "debug_loop":
            if not project_path:
                return json.dumps({"error": "No project specified"})
            from luxar.tools.debug_loop_project import run_debug_loop_project
            result = run_debug_loop_project(
                project_path=str(project_path),
                config=cfg,
                project_root=str(cm.project_root()),
                probe=args.get("probe"),
                port=args.get("port", ""),
                clean=args.get("clean", False),
            )
            return json.dumps({"result": str(result)}, ensure_ascii=False)

        if name == "review_project":
            if not project_path or not project_path.exists():
                return json.dumps({"error": f"Project '{project}' not found"})
            engine = ReviewEngine(str(project_path))
            file = args.get("file", "")
            if file:
                report = engine.review_file(str(project_path / file))
            else:
                report = engine.review_project()
            return json.dumps(report.model_dump(mode="json") if hasattr(report, "model_dump") else {"report": str(report)}, ensure_ascii=False)

        if name == "fix_code":
            if not project_path:
                return json.dumps({"error": "No project specified"})
            file = args.get("file", "")
            if not file:
                return json.dumps({"error": "No file specified"})
            # Reject edits to Core/ files without USER CODE markers (CubeMX generated)
            try:
                content = (project_path / file).read_text(encoding="utf-8")
                if "core" in str(Path(file).parts).lower() and "USER CODE BEGIN" not in content:
                    return json.dumps({"error": f"Cannot auto-fix CubeMX-generated file '{file}'. Only App/ files and Core/ files with USER CODE sections are editable."})
            except Exception:
                pass
            fixer = CodeFixer(cfg)
            result = fixer.fix_file(
                project_path=str(project_path),
                file_path=str(project_path / file),
                apply_changes=not args.get("dry_run", False),
            )
            return json.dumps({"result": str(result)}, ensure_ascii=False)

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
            return json.dumps(result.model_dump(mode="json") if hasattr(result, "model_dump") else {"result": str(result)}, ensure_ascii=False)

        return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as e:
        return json.dumps({"error": f"Tool '{name}' failed: {e}"}, ensure_ascii=False)


def _truncate_tool_result(result: str, max_chars: int = 3000) -> str:
    """Prevent bloating context with huge JSON tool results."""
    if len(result) <= max_chars:
        return result
    return result[:max_chars] + f"\n... [truncated from {len(result)} chars]"


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
- When the user says "审查" (review) → directly call review_project.
- When the user says "构建" (build) → directly call build_project.
- When the user says "状态" (status) → call project_context (it includes status, git, and files).
- Do NOT call multiple exploratory tools before the one the user asked for. Just call the right tool directly.

## CubeMX Rules (CRITICAL)
- This is an STM32CubeMX project. Core/ files (main.c, freertos.c, stm32*.c, system_*.c, syscalls.c, sysmem.c, *_hal_msp.c) are GENERATED by CubeMX.
- You may ONLY edit code inside existing /* USER CODE BEGIN ... */ ... /* USER CODE END */ blocks.
- NEVER create new USER CODE sections in Core/ files — this will break the CubeMX workflow.
- NEVER add, remove, or modify code OUTSIDE of USER CODE blocks in Core/ files.
- If the user asks you to edit Core/ files outside USER CODE blocks, explain that it will be overwritten by CubeMX regeneration and refuse.
- App/ files (App/Src/*, App/Inc/*) are fully editable — they are user code.
- If the user says "审查", call ONLY review_project. Do NOT call project_context/project_status/project_files first.

## Language
- Respond in the same language the user uses. Chinese in -> Chinese out.

## Conversation
- Chat naturally. For casual conversation — respond directly.

## Tool usage
- You have tools for build, flash, review, forge, debug loop, git, etc.
- Call a tool only when the user explicitly asks for an action.
- Summarize tool results in natural language.
- Be concise."""

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
- Do NOT call tools for casual conversation or questions.
- After a tool executes, summarize the result in natural language for the user.
- Be concise and helpful."""


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
    if not _conv_store:
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
    for m in conv[-20:]:
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
    """Final pass: ensure every tool message is preceded by assistant with tool_calls.
    This is a safety net — handles edge cases from compression, old data, etc."""
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
    return clean


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
        if repaired == api_messages:
            continue
        try:
            return client.complete_with_tools(messages=repaired, tools=TOOLS), repaired, None
        except Exception as retry_exc:
            if not _is_reasoning_handoff_error(retry_exc):
                return None, repaired, retry_exc
    return None, api_messages, None


async def _run_agent_loop(
    conv: list[dict],
    msg_content: str,
    project: str,
    cfg: Any,
    cm: ConfigManager,
    client: Any,
    docs: list | None = None,
) -> dict[str, str]:
    api_messages = _prepare_agent_context(conv, msg_content, project, cfg, cm, client, docs)

    max_rounds = 20
    for _ in range(max_rounds):
        try:
            resp = client.complete_with_tools(messages=api_messages, tools=TOOLS)
        except Exception as e:
            if _is_reasoning_handoff_error(e):
                resp, repaired, retry_error = _retry_after_reasoning_handoff_repair(client, api_messages)
                if resp is None:
                    return {"content": f"Error calling LLM: {retry_error or e}", "reasoning_content": ""}
                api_messages = repaired
            else:
                return {"content": f"Error calling LLM: {e}", "reasoning_content": ""}

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
                result = _execute_tool(tc.function_name, tc.arguments, cfg, cm)
                result = _truncate_tool_result(result)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
                api_messages.append(tool_msg)
                conv.append({
                    "id": str(uuid.uuid4()),
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
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
    api_messages = _prepare_agent_context(conv, msg_content, project, cfg, cm, client, docs)

    max_rounds = 20
    final_content = ""
    final_reasoning = ""
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
            if _is_reasoning_handoff_error(e):
                repaired = _repair_messages_for_reasoning_handoff(api_messages, aggressive=False)
                if repaired == api_messages:
                    repaired = _repair_messages_for_reasoning_handoff(api_messages, aggressive=True)
                if repaired != api_messages:
                    api_messages = repaired
                    yield {"event": "warning", "data": json.dumps({"warning": "Recovered from stale reasoning context and retried."})}
                    continue
            yield {"event": "error", "data": json.dumps({"error": str(e)})}
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
            yield {"event": "tool_running", "data": json.dumps({"tool": collected_tc_name})}
            result = _execute_tool(collected_tc_name, args, cfg, cm)
            result = _truncate_tool_result(result)
            tool_msg = {
                "role": "tool",
                "tool_call_id": collected_tc_id,
                "content": result,
            }
            api_messages.append(tool_msg)
            conv.append({
                "id": str(uuid.uuid4()),
                "role": "tool",
                "tool_call_id": collected_tc_id,
                "content": result,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            yield {"event": "tool_result", "data": json.dumps({"tool": collected_tc_name, "result": result})}
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

        user_msg = {
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": msg_content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        conv.append(user_msg)
        docs = body.get("docs", []) or []

        from luxar.core.llm_client import LLMClient
        client = LLMClient(cfg)

        if stream:
            return EventSourceResponse(_run_agent_loop_stream(conv, msg_content, project, cfg, cm, client, docs))
        else:
            reply = await _run_agent_loop(conv, msg_content, project, cfg, cm, client, docs)
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

