from __future__ import annotations

import copy
import json
from typing import Any

from pydantic import BaseModel, Field

from luxar.core.config_manager import ConfigManager
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
from luxar.tools.workspace_tool import (
    workspace_build,
    workspace_create_project,
    workspace_flash,
    workspace_inspect,
    workspace_list_projects,
    workspace_monitor,
    workspace_monitor_start,
    workspace_monitor_stop,
    workspace_monitor_status,
    workspace_probe,
    workspace_read_file,
    workspace_write_file,
    workspace_shell,
    workspace_publish_driver,
)
from luxar.tools.analyze_doc import analyze_document_engineering


class ToolExecutionEnvelope(BaseModel):
    ok: bool
    tool: str
    data: Any
    error: str = ""
    summary_source: dict = Field(default_factory=dict)
    truncated: bool = False


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    return value


def is_tool_result_failure(result: Any) -> bool:
    if isinstance(result, ToolExecutionEnvelope):
        data = result.data
        if isinstance(data, dict) and data.get("blocked") is True:
            return False
        if result.error:
            return True
        if result.ok is False:
            return True
        return is_tool_result_failure(data)
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


def build_tool_envelope(
    name: str,
    data: Any | None = None,
    *,
    error: str = "",
    summary_source: dict | None = None,
    truncated: bool = False,
) -> ToolExecutionEnvelope:
    payload = to_jsonable(data if data is not None else {})
    source = to_jsonable(summary_source if summary_source is not None else payload)
    envelope = ToolExecutionEnvelope(
        ok=not is_tool_result_failure(payload),
        tool=name,
        data=payload,
        error=error or (payload.get("error", "") if isinstance(payload, dict) else ""),
        summary_source=source if isinstance(source, dict) else {},
        truncated=truncated,
    )
    if envelope.error and not isinstance(envelope.data, dict):
        envelope.data = {"result": envelope.data, "error": envelope.error}
    return envelope


def parse_tool_result(result: Any) -> ToolExecutionEnvelope:
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
        return build_tool_envelope("unknown", payload)
    return build_tool_envelope("unknown", result)


def compact_tool_payload(
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
            compact_tool_payload(item, aggressive=aggressive, parent_key=parent_key, truncation_state=truncation_state)
            for item in items
        ]

    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > dict_limit:
            truncation_state["truncated"] = True
            items = items[:dict_limit]
        return {
            key: compact_tool_payload(item, aggressive=aggressive, parent_key=str(key), truncation_state=truncation_state)
            for key, item in items
        }

    return value


def serialize_tool_data(data: Any) -> str:
    return json.dumps(to_jsonable(data), ensure_ascii=False)


def serialize_tool_content_for_llm(envelope: ToolExecutionEnvelope, max_chars: int = 3000) -> str:
    truncation_state = {"truncated": False}
    compacted = compact_tool_payload(copy.deepcopy(envelope.data), truncation_state=truncation_state)
    text = serialize_tool_data(compacted)
    if len(text) <= max_chars:
        return text

    truncation_state = {"truncated": False}
    compacted = compact_tool_payload(
        copy.deepcopy(envelope.summary_source or envelope.data),
        aggressive=True,
        truncation_state=truncation_state,
    )
    text = serialize_tool_data(compacted)
    if len(text) <= max_chars:
        return text

    fallback = {
        "success": envelope.ok,
        "tool": envelope.tool,
        "error": envelope.error,
        "truncated": True,
        "summary_source": compact_tool_payload(
            copy.deepcopy(envelope.summary_source or envelope.data),
            aggressive=True,
            truncation_state={"truncated": False},
        ),
    }
    text = serialize_tool_data(fallback)
    if len(text) <= max_chars:
        return text

    minimal = {
        "success": envelope.ok,
        "tool": envelope.tool,
        "error": envelope.error[:500] if envelope.error else "",
        "truncated": True,
    }
    return serialize_tool_data(minimal)


def format_tool_result_summary(name: str, args: dict, result: Any) -> tuple[bool, str]:
    envelope = parse_tool_result(result)
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
    if name == "workspace_list_projects":
        projects = data.get("projects") or []
        return True, f"已列出 {len(projects)} 个项目"
    if name == "workspace_create_project":
        proj = data.get("project") or {}
        return is_ok, f"项目 {proj.get('name', '?')} 已创建" if is_ok else f"项目创建失败: {data.get('error','')}"
    if name == "workspace_read_file":
        if is_ok:
            sz = data.get("size", 0)
            path = args.get("path", "?")
            sz = data.get("size", 0)
            return True, f"Read {path} ({sz} chars)"
        return False, f"文件读取失败: {data.get('error','')}"

    if name == "workspace_build":
        if is_ok:
            return True, "构建成功"
        stderr = data.get('stderr') or ''
        stdout = data.get('stdout') or ''
        combined = (stderr + '\n' + stdout).strip()
        if combined:
            output = combined[-2000:] if len(combined) > 2000 else combined
            return False, '构建失败，提示内容：\n' + output
        errors = data.get('errors') or []
        if errors:
            return False, '构建失败: ' + '; '.join(str(e)[:200] for e in errors[:10])
        error_msg = data.get('error') or data.get('message', '') or '编译出错'
        return False, '构建失败： ' + str(error_msg)[:500]
    if name == "workspace_flash":
        return is_ok, "烧录成功" if is_ok else f"烧录失败: {data.get('error','')}"
    if name == "workspace_monitor":
        return True, "串口监控已启动"
    if name == "workspace_monitor_start":
        return is_ok, "串口监听已启动" if is_ok else f"启动失败: {data.get('error','')}"
    if name == "workspace_monitor_stop":
        return True, "串口监听已停止"
    if name == "workspace_monitor_status":
        state = data.get("state", "unknown")
        port = data.get("port", "")
        return True, f"监听状态: {state} @ {port}"
    if name == "workspace_probe":
        probe_type = data.get("probe_type") or args.get("probe_type", "")
        return is_ok, f"{probe_type or 'workspace'} 探测已执行"
    if name == "workspace_publish_driver":
        if is_ok:
            chip = data.get("chip", "")
            variant = data.get("variant", "")
            target = data.get("target_path", "")
            return True, f"Driver published: {chip}/{variant}"
        msg = data.get("message", "")
        if data.get("existing"):
            return False, f"Already exists: {msg}"
        if data.get("needs_variant"):
            return False, f"Variant needed: {msg}"
        return False, f"Publish failed: {msg or data.get('error', '')}"

    if name == "analyze_document_engineering":
        pin_count = len(data.get("pin_requirements") or [])
        bus_count = len(data.get("bus_requirements") or [])
        parse_errors = data.get("parse_errors") or []
        parts = []
        if pin_count:
            parts.append(f"{pin_count} 个引脚需求")
        if bus_count:
            parts.append(f"{bus_count} 个总线接口")
        if parse_errors:
            parts.append(f"{len(parse_errors)} 个解析错误")
        msg = "文档分析完成" + ("：" + ", ".join(parts) if parts else "")
        return is_ok, msg


    if name == "workspace_inspect":
        return is_ok, "工作区状态已读取"
    if name == "search_driver":
        count = len(data.get("results", []))
        total = data.get("stats", {}).get("total_drivers", 0)
        kw = data.get("keyword", "")
        if kw:
            return is_ok, f"Found {count} drivers for '{kw}' (of {total} total)"
        return is_ok, f"Driver library: {count} results, {total} total"

    if is_ok:
        return True, f"工具 '{name}' 已完成"
    error_msg = data.get("error") or data.get("message", "")
    return False, f"工具 '{name}' 返回失败: {str(error_msg)[:80]}"


_TOOL_NAME_CORRECTIONS: dict[str, str] = {
    "workshell": "workspace_shell",
}


def correct_tool_name(name: str) -> str:
    """Silently correct known LLM typos in tool names."""
    return _TOOL_NAME_CORRECTIONS.get(name, name)


def validate_public_tool_name(name: str, public_tool_names: set[str] | frozenset[str]) -> ToolExecutionEnvelope | None:
    if name in public_tool_names:
        return None
    message = (
        f"Tool '{name}' is not part of the LUXAR 0.2.0 public control plane. "
        "Use runtime/skills/memory/workspace primitives instead."
    )
    return build_tool_envelope(name, {"error": message}, error=message)


def execute_tool(name: str, args: dict, cfg: Any, cm: ConfigManager, public_tool_names: set[str] | frozenset[str]) -> ToolExecutionEnvelope:
    try:
        invalid_tool = validate_public_tool_name(name, public_tool_names)
        if invalid_tool is not None:
            return invalid_tool
        if name == "runtime_run":
            return build_tool_envelope(name, run_runtime(task=args.get("task", ""), project=args.get("project", "")))
        if name == "runtime_explain":
            return build_tool_envelope(name, explain_runtime_tool())
        if name == "skills_list":
            return build_tool_envelope(name, vnext_skills_list(category=args.get("category")))
        if name == "skill_view":
            return build_tool_envelope(name, skill_view(name=args.get("name", "")))
        if name == "skill_manage":
            return build_tool_envelope(
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
            return build_tool_envelope(
                name,
                skill_promote(
                    name=args.get("name", ""),
                    category=args.get("category", ""),
                    promotion_level=args.get("promotion_level", "validated"),
                ),
            )
        if name == "search_driver":
            from luxar.tools.search_driver import run_search_driver
            cm = ConfigManager()
            cfg = cm.ensure_default_config()
            return build_tool_envelope(
                name,
                run_search_driver(
                    config=cfg,
                    project_root=str(cm.project_root()),
                    keyword=str(args.get("keyword", "")),
                    protocol=str(args.get("protocol", "")),
                    vendor=str(args.get("vendor", "")),
                    limit=int(args.get("limit", 20)),
                ),
            )

        if name == "skill_execute":
            return build_tool_envelope(
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
            return build_tool_envelope(name, memory_read(target=args.get("target", "memory")))
        if name == "memory_write":
            return build_tool_envelope(
                name,
                memory_write(
                    content=args.get("content", ""),
                    target=args.get("target", "memory"),
                    append=bool(args.get("append", True)),
                ),
            )
        if name == "memory_search":
            return build_tool_envelope(name, memory_search(query=args.get("query", "")))
        if name == "lesson_list":
            return build_tool_envelope(name, memory_lessons())
        if name == "lesson_search":
            return build_tool_envelope(name, memory_lessons(query=args.get("query", ""), limit=int(args.get("limit", 5))))
        if name == "lesson_record":
            return build_tool_envelope(
                name,
                memory_lesson_record(payload=args.get("payload", {}) or {}, promoted=bool(args.get("promoted", False))),
            )
        if name == "lesson_promote":
            return build_tool_envelope(
                name,
                memory_lesson_promote(slug=args.get("slug", ""), evidence_count=int(args.get("evidence_count", 1))),
            )
        if name == "workspace_inspect":
            return build_tool_envelope(name, workspace_inspect())
        if name == "workspace_list_projects":
            return build_tool_envelope(name, workspace_list_projects())
        if name == "workspace_create_project":
            return build_tool_envelope(
                name,
                workspace_create_project(
                    name=str(args.get("name", "")),
                    mcu=str(args.get("mcu", "STM32F103C8")),
                    platform=str(args.get("platform", "stm32cubemx")),
                    runtime=str(args.get("runtime", "baremetal")),
                    firmware_package=str(args.get("firmware_package", "")),
                ),
            )
        if name == "workspace_read_file":
            return build_tool_envelope(
                name,
                workspace_read_file(
                    project=str(args.get("project", "")),
                    path=str(args.get("path", "")),
                ),
            )
        if name == "workspace_write_file":
            return build_tool_envelope(
                name,
                workspace_write_file(
                    project=str(args.get("project", "")),
                    path=str(args.get("path", "")),
                    content=str(args.get("content", "")),
                ),
            )
        if name == "workspace_build":
            return build_tool_envelope(name, workspace_build(project=args.get("project", ""), clean=bool(args.get("clean", False))))
        if name == "workspace_flash":
            return build_tool_envelope(name, workspace_flash(project=args.get("project", ""), probe=args.get("probe", "")))
        if name == "workspace_monitor":
            return build_tool_envelope(
                name,
                workspace_monitor(
                    project=args.get("project", ""),
                    port=args.get("port", ""),
                    baudrate=int(args.get("baudrate", 115200)),
                ),
            )
        if name == "workspace_monitor_start":
            return build_tool_envelope(
                name,
                workspace_monitor_start(
                    project=args.get("project", ""),
                    port=args.get("port", ""),
                    baudrate=int(args.get("baudrate", 115200)),
                ),
            )
        if name == "workspace_monitor_stop":
            return build_tool_envelope(
                name,
                workspace_monitor_stop(
                    project=args.get("project", ""),
                ),
            )
        if name == "workspace_monitor_status":
            return build_tool_envelope(
                name,
                workspace_monitor_status(
                    project=args.get("project", ""),
                ),
            )
        if name == "workspace_shell":
            return build_tool_envelope(
                name,
                workspace_shell(
                    project=str(args.get("project", "")),
                    command=str(args.get("command", "")),
                ),
            )
        if name == "workspace_probe":
            return build_tool_envelope(
                name,
                workspace_probe(project=args.get("project", ""), probe_type=args.get("probe_type", "i2c")),
            )

        if name == "workspace_publish_driver":
            return build_tool_envelope(
                name,
                workspace_publish_driver(
                    project=str(args.get("project", "")),
                    header_path=str(args.get("header_path", "")),
                    source_path=str(args.get("source_path", "")),
                    variant=str(args.get("variant", "")),
                    force=bool(args.get("force", False)),
                ),
            )

        if name == "analyze_document_engineering":
            return build_tool_envelope(
                name,
                analyze_document_engineering(
                    docs=list(args.get("docs", [])),
                    query=str(args.get("query", "")),
                    cm=cm,
                ),
            )
        return build_tool_envelope(name, {"error": f"Unknown tool: {name}"}, error=f"Unknown tool: {name}")
    except Exception as e:
        message = f"Tool '{name}' failed: {e}"
        return build_tool_envelope(name, {"error": message}, error=message)


def build_tool_running_payload(name: str, args: dict) -> dict:
    payload = {"tool": name}
    task = args.get("task", "")
    project = args.get("project", "")
    if task:
        payload["task"] = task
    if project:
        payload["project"] = project
    return payload