from __future__ import annotations

import json

from typing import Any

from luxar.core.config_manager import ConfigManager
from luxar.core.context_compressor import ContextCompressor
from luxar.core.llm_client import _OPENAI_PROVIDERS


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
- When calling workspace_read_file or workspace_write_file, always pass the project name (currently "{project}") as the "project" parameter. Never pass an empty string or omit this parameter.
- Never call workspace_read_file on build artifacts (build/**, CMakeFiles/**, *.o, *.elf) unless you have already confirmed via workspace_build that the file was produced. If workspace_read_file returns "File not found", treat it as evidence the file does not exist — do not retry.
- Prefer workspace_shell with "type" (Windows) or "cat" (Unix) to read files — it returns full content without truncation. Use workspace_read_file only as a fallback.
"""

GLOBAL_SYSTEM_PROMPT = """\
You are LUXAR v0.2.0.

- Respond in the same language as the user.
- Harness is the runtime behavior system. Use runtime, skills, memory, and workspace primitives only.
- Skills are the only procedural artifacts. Memory stores stable facts. Lessons store unpromoted experience.
- Do not fabricate evidence, hardware state, or tool results.
- When calling workspace_read_file or workspace_write_file, always pass the current project name as the "project" parameter. If no project is selected in the sidebar, tell the user to select one first.
- For casual conversation or explanation-only requests, respond directly without tools.
- For concrete actions, use the smallest appropriate primitive and summarize the evidence-backed result.
- When calling workspace_read_file or workspace_write_file, always pass the project name (currently "{project}") as the "project" parameter. Never pass an empty string or omit this parameter.
- Never call workspace_read_file on build artifacts (build/**, CMakeFiles/**, *.o, *.elf) unless you have already confirmed via workspace_build that the file was produced. If workspace_read_file returns "File not found", treat it as evidence the file does not exist — do not retry.
- Prefer workspace_shell with "type" (Windows) or "cat" (Unix) to read files — it returns full content without truncation. Use workspace_read_file only as a fallback.
"""


def get_context_limit(cfg: Any) -> int:
    provider = cfg.llm.provider.strip().lower()
    model = cfg.llm.model
    info = _OPENAI_PROVIDERS.get(provider, {})
    for model_info in info.get("models", []):
        if model_info["id"] == model:
            return model_info.get("context", 4096)
    if provider == "claude":
        return 200000
    return 4096


def inject_environment_info(base_prompt: str, cm: ConfigManager) -> str:
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
    return (
        base_prompt
        + "\n\n## Available Toolchains (already configured on this machine)\n"
        + "\n".join(lines)
        + "\nDo NOT claim toolchains are missing —they are available at these paths."
    )


def enrich_system_prompt(
    base_prompt: str,
    msg_content: str,
    conv_store: Any,
    docs: list | None = None,
    project: str = "",
) -> str:
    enriched = base_prompt
    if docs:
        doc_paths = [d if isinstance(d, str) else d.get("path", str(d)) for d in docs]
        enriched += (
            f"\n\nThe user has attached documents: {", ".join(doc_paths)}.\n"
            "Call analyze_document_engineering exactly once. If the call returns success, do NOT call it again — one call extracts all facts.\n"
        )
    if not conv_store or not project:
        return enriched
    try:
        related = conv_store.search(query=msg_content, project=project or None, limit=3)
    except Exception:
        return enriched
    if not related:
        return enriched
    lines = ["\n## Relevant history from your past conversations"]
    for item in related:
        role = item.get("role", "?")
        content = (item.get("content", "") or "")[:200]
        lines.append(f"- [{role}]: {content}")
    return enriched + "\n" + "\n".join(lines) + "\n"


def truncate_with_tool_pairing(conv: list[dict], max_keep: int = 20) -> list[dict]:
    if len(conv) <= max_keep:
        return list(conv)
    start = len(conv) - max_keep
    orphan_ids: set[str] = set()
    for message in conv[start:]:
        if message.get("role") == "assistant":
            for tool_call in message.get("tool_calls") or []:
                tool_call_id = isinstance(tool_call, dict) and tool_call.get("id") or tool_call
                if tool_call_id:
                    orphan_ids.add(tool_call_id)
    for message in conv[start:]:
        tool_call_id = message.get("tool_call_id")
        if tool_call_id:
            orphan_ids.discard(tool_call_id)
    if orphan_ids:
        for index in range(start - 1, -1, -1):
            tool_call_id = conv[index].get("tool_call_id")
            if tool_call_id and tool_call_id in orphan_ids:
                orphan_ids.discard(tool_call_id)
                start = index
                if not orphan_ids:
                    break
    return list(conv[start:])


def strip_orphan_tool_calls(msgs: list[dict]) -> list[dict]:
    known_tool_ids: set[str] = {
        message["tool_call_id"]
        for message in msgs
        if message["role"] == "tool" and message.get("tool_call_id")
    }
    for message in msgs:
        if message["role"] == "assistant" and message.get("tool_calls"):
            filtered = [tool_call for tool_call in message["tool_calls"] if tool_call.get("id") in known_tool_ids]
            if filtered:
                message["tool_calls"] = filtered
            else:
                message.pop("tool_calls", None)
                message.setdefault("content", "")
    return [msg for msg in msgs if msg.get("content") or msg.get("tool_calls")]


def validate_api_messages(msgs: list[dict]) -> list[dict]:
    clean: list[dict] = []
    for message in msgs:
        if message["role"] == "tool" and message.get("tool_call_id"):
            # Look backwards to find the most recent assistant with tool_calls
            prev_assistant = None
            for m in reversed(clean):
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    prev_assistant = m
                    break
            has_tc = prev_assistant and any(
                tc.get("id") == message["tool_call_id"]
                for tc in prev_assistant["tool_calls"]
            )
            if not has_tc:
                # Only inject if this tool_call_id isn't already covered by a previous assistant
                if not clean or clean[-1]["role"] != "assistant":
                    clean.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": message["tool_call_id"],
                                    "type": "function",
                                    "function": {"name": message.get("tool_name", "unknown"), "arguments": "{}"},
                                }
                            ],
                        }
                    )
                elif "tool_calls" not in clean[-1]:
                    clean[-1]["tool_calls"] = [
                        {
                            "id": message["tool_call_id"],
                            "type": "function",
                            "function": {"name": message.get("tool_name", "unknown"), "arguments": "{}"},
                        }
                    ]
        clean.append(message)
    return strip_orphan_tool_calls(clean)



def inject_project_metadata(base_prompt: str, project: str, cm: ConfigManager) -> str:
    """Inject project metadata into the system prompt."""
    workspace = cm.workspace_root()
    projects_dir = workspace / "projects"

    if project:
        try:
            proj_meta_path = projects_dir / project / ".agent_project.json"
            if not proj_meta_path.exists():
                return base_prompt
            meta = json.loads(proj_meta_path.read_text(encoding="utf-8"))
        except Exception:
            return base_prompt
        lines = _build_meta_lines(meta)
        if not lines:
            return base_prompt
        return (
            base_prompt
            + "\n\n## Current Project (user already specified these \u2014 do NOT ask again)\n"
            + "\n".join(lines)
        )
    else:
        if not projects_dir.exists():
            return base_prompt
        project_dirs = sorted(
            [d for d in projects_dir.iterdir() if d.is_dir() and (d / ".agent_project.json").exists()]
        )
        if not project_dirs:
            return base_prompt
        project_list = []
        for proj_dir in project_dirs:
            try:
                meta = json.loads((proj_dir / ".agent_project.json").read_text(encoding="utf-8"))
                name = meta.get("name", proj_dir.name)
                mcu = meta.get("mcu", "?")
                platform = meta.get("platform", "?")
                runtime = meta.get("runtime", "?")
                project_list.append(f"- {name}: MCU={mcu}, Platform={platform}, Runtime={runtime}")
            except Exception:
                project_list.append(f"- {proj_dir.name}")
        return (
            base_prompt
            + "\n\n## Available Projects (each already configured by user \u2014 do NOT ask about MCU/platform again)\n"
            + "\n".join(project_list)
        )


def _build_meta_lines(meta: dict) -> list[str]:
    lines = []
    for key, label in [("mcu", "MCU"), ("platform", "Platform"), ("runtime", "Runtime"), ("firmware_package", "Firmware Package")]:
        v = meta.get(key, "")
        if v:
            lines.append(f"- {label}: {v}")
    return lines


def prepare_agent_context(
    conv: list[dict],
    msg_content: str,
    project: str,
    cfg: Any,
    cm: ConfigManager,
    client: Any,
    conv_store: Any,
    docs: list | None = None,
) -> list[dict]:
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(project=project) if project else GLOBAL_SYSTEM_PROMPT
    system_prompt = enrich_system_prompt(system_prompt, msg_content, conv_store, docs, project)
    
    system_prompt = inject_environment_info(system_prompt, cm)
    system_prompt = inject_project_metadata(system_prompt, project, cm)

    ctx_limit = get_context_limit(cfg)
    compressor = ContextCompressor(context_limit=ctx_limit)
    if compressor.should_compress(conv):
        conv[:] = compressor.compress(conv, client)

    api_messages: list[dict] = [{"role": "system", "content": system_prompt}]
    recent = truncate_with_tool_pairing(conv, max_keep=20)
    for message in recent:
        entry: dict = {"role": message["role"], "content": message.get("content", "")}
        if message.get("tool_call_id"):
            entry["role"] = "tool"
            entry["tool_call_id"] = message["tool_call_id"]
            tc_fix = [
                {
                    "id": message["tool_call_id"],
                    "type": "function",
                    "function": {"name": message.get("tool_name", "unknown"), "arguments": "{}"},
                }
            ]
            # Look backwards to find the most recent assistant with tool_calls
            prev_assistant = None
            for m in reversed(api_messages):
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    prev_assistant = m
                    break
            has_tc = prev_assistant and any(
                tc.get("id") == message["tool_call_id"]
                for tc in prev_assistant["tool_calls"]
            )
            if not has_tc:
                api_messages.append({"role": "assistant", "tool_calls": tc_fix})
        if message.get("tool_calls"):
            entry["tool_calls"] = message["tool_calls"]
        if message.get("reasoning_content"):
            entry["reasoning_content"] = message["reasoning_content"]
        api_messages.append(entry)
    return validate_api_messages(api_messages)


def is_reasoning_handoff_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "reasoning_content" in message and "must be passed back" in message


def sanitize_reasoning_message(message: dict) -> dict:
    clean = dict(message)
    if not clean.get("reasoning_content"):
        clean.pop("reasoning_content", None)
    return clean


def repair_messages_for_reasoning_handoff(api_messages: list[dict], aggressive: bool = False) -> list[dict]:
    if aggressive:
        repaired = []
        for index, message in enumerate(api_messages):
            clean = sanitize_reasoning_message(message)
            if index == 0 or clean.get("role") == "user":
                repaired.append(clean)
        if repaired:
            user_messages = [msg for msg in repaired[1:] if msg.get("role") == "user"]
            repaired = [repaired[0]] + user_messages[-6:]
        return repaired

    repaired: list[dict] = []
    dropped_tool_call_ids: set[str] = set()
    for index, message in enumerate(api_messages):
        clean = sanitize_reasoning_message(message)
        if index == 0:
            repaired.append(clean)
            continue
        if clean.get("role") == "assistant" and not clean.get("reasoning_content"):
            for tool_call in clean.get("tool_calls") or []:
                tool_call_id = tool_call.get("id")
                if tool_call_id:
                    dropped_tool_call_ids.add(tool_call_id)
            continue
        if clean.get("role") == "tool" and clean.get("tool_call_id") in dropped_tool_call_ids:
            continue
        repaired.append(clean)
    return validate_api_messages(repaired)


def retry_after_reasoning_handoff_repair(
    client: Any,
    api_messages: list[dict],
    tools: list[dict],
) -> tuple[Any | None, list[dict], Exception | None]:
    for aggressive in (False, True):
        repaired = repair_messages_for_reasoning_handoff(api_messages, aggressive=aggressive)
        try:
            return client.complete_with_tools(messages=repaired, tools=tools), repaired, None
        except Exception as retry_exc:
            if not is_reasoning_handoff_error(retry_exc):
                return None, repaired, retry_exc
    return None, api_messages, None
