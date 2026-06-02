from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from luxar.core.config_manager import ConfigManager, LLMSection
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
    workspace_hw_probe,
    workspace_probe,
    workspace_status,
    workspace_uart_gate,
)
from luxar.server.legacy_surface import (
    normalize_project_name,
    register_legacy_conversation_surface,
    register_legacy_http_surface,
)
from luxar.server.chat_support import (
    is_reasoning_handoff_error as _is_reasoning_handoff_error,
    prepare_agent_context as _prepare_agent_context,
    repair_messages_for_reasoning_handoff as _repair_messages_for_reasoning_handoff,
    retry_after_reasoning_handoff_repair as _retry_after_reasoning_handoff_repair,
)
from luxar.server.conversation_state import ConversationState
from luxar.server.agent_loop_runner import (
    run_agent_loop as _run_agent_loop_impl,
    run_agent_loop_stream as _run_agent_loop_stream_impl,
)
from luxar.server.tool_execution import (
    ToolExecutionEnvelope,
    build_tool_envelope as _build_tool_envelope,
    build_tool_running_payload as _build_tool_running_payload,
    execute_tool as _execute_tool_impl,
    format_tool_result_summary as _format_tool_result_summary,
    is_tool_result_failure as _is_tool_result_failure,
    parse_tool_result as _parse_tool_result,
    serialize_tool_content_for_llm as _serialize_tool_content_for_llm,
    serialize_tool_data as _serialize_tool_data,
    correct_tool_name as _correct_tool_name,
    validate_public_tool_name as _validate_public_tool_name_impl,
)
from luxar.server.tool_schema import PUBLIC_TOOL_NAMES, TOOLS
from luxar.server.tool_runtime import (
    AgentToolLimitError,
    AgentToolTimeoutError,
    MAX_AGENT_TOOL_CALLS,
    MAX_AGENT_TOOL_TIMEOUT_SEC,
    TOOL_TIMEOUT_OVERRIDES as _TOOL_TIMEOUT_OVERRIDES,
    enforce_tool_call_budget as _enforce_tool_call_budget_impl,
    execute_tool_with_timeout as _execute_tool_with_timeout_impl,
)
from luxar.server.vnext_surface import register_vnext_http_surface
from luxar.server.app_shell import register_app_shell_surface
from luxar.server.project_status import project_status as _project_status_impl
from luxar.server.skill_extraction import try_extract_skill as _try_extract_skill_impl


# ===== Tool Definitions (OpenAI Function Calling schema) =====

LEGACY_HTTP_SURFACE_ENV = "LUXAR_ENABLE_LEGACY_HTTP_SURFACE"

MAX_CONSECUTIVE_TOOL_FAILURES = 10

RETAINED_LEGACY_APP_EXPORTS = (
    "LEGACY_HTTP_SURFACE_ENV",
)

PUBLIC_APP_EXPORTS = (
    "PUBLIC_TOOL_NAMES",
    "create_app",
)


_agent_log = logging.getLogger("luxar.agent")


def _build_consecutive_failure_limit_message(failures: int) -> str:
    return (
        f"连续 {failures} 次工具调用返回了失败结果。"
        f"我停止继续尝试，以免造成更多问题。请检查项目状态并告诉我下一步怎么做。"
    )


def _validate_public_tool_name(name: str) -> ToolExecutionEnvelope | None:
    return _validate_public_tool_name_impl(name, PUBLIC_TOOL_NAMES)


def _execute_tool(name: str, args: dict, cfg: Any, cm: ConfigManager) -> ToolExecutionEnvelope:
    return _execute_tool_impl(name, args, cfg, cm, PUBLIC_TOOL_NAMES)


def _enforce_tool_call_budget(tool_name: str, used_calls: int) -> int:
    return _enforce_tool_call_budget_impl(tool_name, used_calls, max_calls=MAX_AGENT_TOOL_CALLS)


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
    return await _execute_tool_with_timeout_impl(
        name,
        args,
        cfg,
        cm,
        execute_tool=_execute_tool,
        parse_tool_result=_parse_tool_result,
        timeout_sec=timeout_sec,
    )


def _legacy_http_surface_enabled() -> bool:
    return os.getenv(LEGACY_HTTP_SURFACE_ENV, "0").strip() == "1"


_conversation_state = ConversationState()


# ===== Agent Loop: LLM reasoning + tool execution =====
async def _run_agent_loop(
    conv: list[dict],
    msg_content: str,
    project: str,
    cfg: Any,
    cm: ConfigManager,
    client: Any,
    docs: list | None = None,
) -> dict[str, str]:
    return await _run_agent_loop_impl(
        conv=conv,
        msg_content=msg_content,
        project=project,
        cfg=cfg,
        cm=cm,
        client=client,
        docs=docs,
        conversation_store=_conversation_state.store,
        prepare_agent_context=_prepare_agent_context,
        is_reasoning_handoff_error=_is_reasoning_handoff_error,
        retry_after_reasoning_handoff_repair=_retry_after_reasoning_handoff_repair,
        correct_tool_name=_correct_tool_name,
        validate_public_tool_name=_validate_public_tool_name,
        execute_tool_with_limits=_execute_tool_with_limits,
        is_tool_result_failure=_is_tool_result_failure,
        max_consecutive_tool_failures=MAX_CONSECUTIVE_TOOL_FAILURES,
        build_consecutive_failure_limit_message=_build_consecutive_failure_limit_message,
        format_tool_result_summary=_format_tool_result_summary,
        serialize_tool_content_for_llm=_serialize_tool_content_for_llm,
        try_extract_skill=_try_extract_skill,
        tools=TOOLS,
    )


def _try_extract_skill(conv: list[dict], project: str, cfg: Any, cm: ConfigManager, client: Any) -> str:
    return _try_extract_skill_impl(conv, project, cm=cm, client=client)


async def _run_agent_loop_stream(
    conv: list[dict],
    msg_content: str,
    project: str,
    cfg: Any,
    cm: ConfigManager,
    client: Any,
    docs: list | None = None,
):
    async for event_payload in _run_agent_loop_stream_impl(
        conv=conv,
        msg_content=msg_content,
        project=project,
        cfg=cfg,
        cm=cm,
        client=client,
        docs=docs,
        conversation_store=_conversation_state.store,
        save_conversation=_conversation_state.save,
        prepare_agent_context=_prepare_agent_context,
        is_reasoning_handoff_error=_is_reasoning_handoff_error,
        repair_messages_for_reasoning_handoff=_repair_messages_for_reasoning_handoff,
        correct_tool_name=_correct_tool_name,
        validate_public_tool_name=_validate_public_tool_name,
        enforce_tool_call_budget=_enforce_tool_call_budget,
        execute_tool_with_timeout=_execute_tool_with_timeout,
        is_tool_result_failure=_is_tool_result_failure,
        max_consecutive_tool_failures=MAX_CONSECUTIVE_TOOL_FAILURES,
        build_consecutive_failure_limit_message=_build_consecutive_failure_limit_message,
        build_tool_running_payload=_build_tool_running_payload,
        serialize_tool_content_for_llm=_serialize_tool_content_for_llm,
        serialize_tool_data=_serialize_tool_data,
        format_tool_result_summary=_format_tool_result_summary,
        tools=TOOLS,
    ):
        yield event_payload


# ===== FastAPI Application Factory =====
def create_app(config_path: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        from luxar.core.monitor_manager import MonitorManager
        try:
            yield
        finally:
            MonitorManager.instance().stop()
            _conversation_state.close()

    app = FastAPI(title="Luxar API", version="0.2.3", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1",
            "http://127.0.0.1:8000",
            "http://localhost",
            "http://localhost:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    cm = ConfigManager(config_path)
    cfg = cm.ensure_default_config()

    legacy_http_surface = _legacy_http_surface_enabled()
    from luxar.core.conversation_store import ConversationStore as _ConversationStore
    _conversation_state.reset_store(_ConversationStore(cm.workspace_root()) if legacy_http_surface else None)

    register_app_shell_surface(app, cfg=cfg, cm=cm)

    register_vnext_http_surface(
        app,
        cfg=cfg,
        cm=cm,
        run_runtime=run_runtime,
        explain_runtime_tool=explain_runtime_tool,
        memory_read=memory_read,
        memory_write=memory_write,
        memory_lessons=memory_lessons,
        memory_lesson_record=memory_lesson_record,
        memory_lesson_promote=memory_lesson_promote,
        memory_search=memory_search,
        workspace_inspect=workspace_inspect,
        workspace_list_projects=workspace_list_projects,
        workspace_create_project=workspace_create_project,
        workspace_build=workspace_build,
        workspace_flash=workspace_flash,
        workspace_monitor=workspace_monitor,
        workspace_monitor_start=workspace_monitor_start,
        workspace_monitor_stop=workspace_monitor_stop,
        workspace_monitor_status=workspace_monitor_status,
        workspace_hw_probe=workspace_hw_probe,
        workspace_probe=workspace_probe,
        workspace_uart_gate=workspace_uart_gate,
        workspace_status=workspace_status,
        skills_list=vnext_skills_list,
        skill_view=skill_view,
        skill_manage=skill_manage,
        skill_promote=skill_promote,
        skill_execute=skill_execute,
    )

    if legacy_http_surface:
        register_legacy_conversation_surface(
            app,
            cfg,
            cm,
            get_conv=_conversation_state.get,
            save_conv=_conversation_state.save,
            run_agent_loop=_run_agent_loop,
            run_agent_loop_stream=_run_agent_loop_stream,
            conv_cache=_conversation_state.cache,
            conv_store=_conversation_state.store,
        )
        register_legacy_http_surface(
            app,
            cfg,
            cm,
            conv_cache=_conversation_state.cache,
            conv_store=_conversation_state.store,
            project_status=_project_status,
        )

    return app


def _project_status(project_path) -> dict:
    from luxar.core.git_manager import GitManager as _GitManager
    return _project_status_impl(project_path, git_manager_cls=_GitManager)


def main():
    import uvicorn
    uvicorn.run("luxar.server.app:create_app", factory=True, host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()


__all__ = (
    *PUBLIC_APP_EXPORTS,
)

