from __future__ import annotations

import json
from typing import Any

from luxar.core.config_manager import ConfigManager
from luxar.server.agent_loop_support import (
    AgentLoopState,
    append_assistant_tool_call_message,
    append_final_assistant_message,
    append_tool_result_message,
    build_reasoning_handoff_retry_events,
    build_stream_tool_result_events,
    check_same_call_loop,
    extract_referenced_portions,
    update_consecutive_failures,
)


def _infer_recent_file_path(conv: list[dict]) -> str:
    """Scan recent messages for the last file path used in read/write."""
    # First pass: scan tool result messages (path is in the result JSON)
    for msg in reversed(conv):
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if not content or '"path"' not in content:
            continue
        try:
            data = json.loads(content)
            path = data.get("path", "")
            if path:
                return path
        except (json.JSONDecodeError, TypeError):
            continue
    # Second pass: scan assistant tool_calls for workspace_read_file / workspace_write_file args
    for msg in reversed(conv):
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            if fn.get("name") in ("workspace_read_file", "workspace_write_file"):
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                    path = args.get("path", "")
                    if path:
                        return path
                except (json.JSONDecodeError, TypeError):
                    continue
    return ""


async def run_agent_loop(
    *,
    conv: list[dict],
    msg_content: str,
    project: str,
    cfg: Any,
    cm: ConfigManager,
    client: Any,
    docs: list | None,
    conversation_store,
    prepare_agent_context,
    is_reasoning_handoff_error,
    retry_after_reasoning_handoff_repair,
    correct_tool_name,
    validate_public_tool_name,
    execute_tool_with_limits,
    is_tool_result_failure,
    max_consecutive_tool_failures: int,
    build_consecutive_failure_limit_message,
    format_tool_result_summary,
    serialize_tool_content_for_llm,
    try_extract_skill,
    tools,
) -> dict[str, str]:
    if hasattr(client, "has_valid_api_key") and not client.has_valid_api_key():
        return {
            "content": (
                f"API key not configured for provider '{cfg.llm.provider}'. "
                "Set the environment variable or add the key in Model Config."
            ),
            "reasoning_content": "",
        }

    api_messages = prepare_agent_context(conv, msg_content, project, cfg, cm, client, conversation_store, docs)
    state = AgentLoopState()
    for _ in range(50):
        try:
            resp = client.complete_with_tools(messages=api_messages, tools=tools)
        except Exception as e:
            # Self-heal: 400 errors → strip orphans + retry
            err_msg = str(e)
            if ('400' in err_msg and ('tool_calls' in err_msg or 'content' in err_msg)
                    and state.repair_count < state.max_repair_attempts):
                state.repair_count += 1
                from luxar.server.chat_support import validate_api_messages
                healed = validate_api_messages(api_messages)
                if healed != api_messages:
                    api_messages[:] = healed
                    conv[:] = validate_api_messages(conv)
                    save_conversation(project)
                    continue
            if is_reasoning_handoff_error(e) and state.repair_count < state.max_repair_attempts:
                state.repair_count += 1
                resp, repaired, retry_error = retry_after_reasoning_handoff_repair(client, api_messages, tools)
                if resp is not None:
                    api_messages = repaired
                    continue
                return {
                    "content": (
                        f"Error calling LLM after {state.max_repair_attempts} recovery attempts: {retry_error or e}. "
                        "Try resetting the conversation or checking the API configuration."
                    ),
                    "reasoning_content": "",
                }
            return {
                "content": f"Error calling LLM: {e}",
                "reasoning_content": "",
            }

        if resp.tool_calls:
            tc_data = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function_name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in resp.tool_calls
            ]
            append_assistant_tool_call_message(
                api_messages,
                conv,
                tc_data,
                content=resp.content or None,
                reasoning_content=resp.reasoning_content or "",
            )
            for tc in resp.tool_calls:
                corrected_name = correct_tool_name(tc.function_name)
                tc.function_name = corrected_name
                invalid_tool = validate_public_tool_name(corrected_name)
                if invalid_tool is not None:
                    return {
                        "content": invalid_tool.error or "Tool is not part of the public control plane.",
                        "reasoning_content": "",
                    }
                try:
                    if tc.function_name in ("workspace_read_file", "workspace_write_file", "workspace_shell") and not tc.arguments.get("project"):
                        tc.arguments["project"] = project
                    if tc.function_name in ("workspace_read_file", "workspace_write_file") and not tc.arguments.get("path"):
                        inferred = _infer_recent_file_path(conv)
                        if inferred:
                            tc.arguments["path"] = inferred
                    # Intercept calls with missing required params before tool execution
                    if tc.function_name == "workspace_shell" and not tc.arguments.get("command"):
                        result = type("_SkipResult", (), {
                            "ok": False, "tool": tc.function_name,
                            "data": {"error": "Missing required parameter 'command'. Provide a shell command like 'type Core/Src/main.c' or 'dir'."},
                            "error": "", "summary_source": {}, "truncated": False,
                        })()
                    elif tc.function_name in ("workspace_read_file", "workspace_write_file") and not tc.arguments.get("path"):
                        result = type("_SkipResult", (), {
                            "ok": False, "tool": tc.function_name,
                            "data": {"error": "Missing required parameter 'path'. Provide a relative file path like Core/Src/main.c."},
                            "error": "", "summary_source": {}, "truncated": False,
                        })()
                    else:
                        result, state.tool_calls_used = await execute_tool_with_limits(
                            tc.function_name,
                            tc.arguments,
                            cfg,
                            cm,
                            used_calls=state.tool_calls_used,
                        )
                except Exception as e:
                    result = type("_ErrorResult", (), {"ok": False, "tool": tc.function_name, "data": {"error": str(e)}, "error": str(e), "summary_source": {}, "truncated": False})()
                # Always append tool result BEFORE any early return, to keep conversation valid
                append_tool_result_message(
                    api_messages,
                    conv,
                    tool_call_id=tc.id,
                    serialized_content=serialize_tool_content_for_llm(result, max_chars=16000 if tc.function_name == "workspace_shell" else 40000 if tc.function_name == "analyze_document_engineering" else 3000),
                )

                # If tool execution raised, stop the loop
                if hasattr(result, "ok") and not result.ok and getattr(result, "error", ""):
                    return {
                        "content": str(getattr(result, "error", "Tool execution failed")),
                        "reasoning_content": "",
                    }

                # Guard: detect same-tool-with-same-args infinite loop
                loop_error = check_same_call_loop(state, tc.function_name, tc.arguments)
                if loop_error:
                    return {
                        "content": loop_error,
                        "reasoning_content": "",
                    }

                state.consecutive_failures, limit_message = update_consecutive_failures(
                    state.consecutive_failures,
                    result,
                    is_failure=is_tool_result_failure,
                    limit=max_consecutive_tool_failures,
                    build_limit_message=build_consecutive_failure_limit_message,
                )
                if limit_message is not None:
                    return {
                        "content": limit_message,
                        "reasoning_content": "",
                    }
                ok, summary = format_tool_result_summary(tc.function_name, tc.arguments, result)
                yield_like_log = (ok, summary, tc.function_name)
                # This dummy assignment keeps the summary evaluation close to the append path.
                _ = yield_like_log
        else:
            return {
                "content": resp.content,
                "reasoning_content": resp.reasoning_content or "",
            }

    return {
        "content": try_extract_skill(conv, project, cfg, cm, client)
        if conv[-1].get("role") == "tool"
        else "I've reached the maximum number of tool call rounds. Please ask me to continue if needed.",
        "reasoning_content": "",
    }


async def run_agent_loop_stream(
    *,
    conv: list[dict],
    msg_content: str,
    project: str,
    cfg: Any,
    cm: ConfigManager,
    client: Any,
    docs: list | None,
    conversation_store,
    save_conversation,
    prepare_agent_context,
    is_reasoning_handoff_error,
    repair_messages_for_reasoning_handoff,
    correct_tool_name,
    validate_public_tool_name,
    enforce_tool_call_budget,
    execute_tool_with_timeout,
    is_tool_result_failure,
    max_consecutive_tool_failures: int,
    build_consecutive_failure_limit_message,
    build_tool_running_payload,
    serialize_tool_content_for_llm,
    serialize_tool_data,
    format_tool_result_summary,
    tools,
) -> Any:
    if hasattr(client, "has_valid_api_key") and not client.has_valid_api_key():
        yield {
            "event": "error",
            "data": json.dumps(
                {
                    "error": (
                        f"API key not configured for provider '{cfg.llm.provider}'. "
                        "Set the environment variable or add the key in Model Config."
                    )
                }
            ),
        }
        return

    api_messages = prepare_agent_context(conv, msg_content, project, cfg, cm, client, conversation_store, docs)
    state = AgentLoopState()
    final_content = ""
    final_reasoning = ""
    pending_outputs: dict[str, dict] = {}
    for _ in range(50):
        round_content = ""
        round_reasoning = ""
        collected_args = ""
        collected_tc_id = ""
        collected_tc_name = ""
        try:
            for event in client.complete_stream(messages=api_messages, tools=tools):
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
            # Self-heal: 400 errors → strip orphans + retry
            err_msg = str(e)
            if ('400' in err_msg and ('tool_calls' in err_msg or 'content' in err_msg)
                    and state.repair_count < state.max_repair_attempts):
                state.repair_count += 1
                from luxar.server.chat_support import validate_api_messages
                healed = validate_api_messages(api_messages)
                if healed != api_messages:
                    api_messages[:] = healed
                    conv[:] = validate_api_messages(conv)
                    save_conversation(project)
                    continue
            if is_reasoning_handoff_error(e) and state.repair_count < state.max_repair_attempts:
                state.repair_count += 1
                aggressive = state.repair_count > 1
                repaired = repair_messages_for_reasoning_handoff(api_messages, aggressive=aggressive)
                if repaired != api_messages:
                    api_messages = repaired
                for event_payload in build_reasoning_handoff_retry_events(state.repair_count, state.max_repair_attempts):
                    yield event_payload
                continue
            yield {
                "event": "error",
                "data": json.dumps(
                    {
                        "error": str(e),
                        "detail": (
                            "The assistant encountered an error. If this persists, "
                            "try resetting the conversation or checking the API key configuration."
                        ),
                    }
                ),
            }
            return

        if collected_tc_name:
            try:
                args = json.loads(collected_args) if collected_args.strip() else {}
            except json.JSONDecodeError:
                # DeepSeek may concatenate multiple JSON objects; extract the first valid one
                try:
                    decoder = json.JSONDecoder()
                    args, _ = decoder.raw_decode(collected_args)
                except Exception:
                    args = {}
            append_assistant_tool_call_message(
                api_messages,
                conv,
                [
                    {
                        "id": collected_tc_id,
                        "type": "function",
                        "function": {"name": collected_tc_name, "arguments": collected_args},
                    }
                ],
                content=None,
                reasoning_content=round_reasoning,
            )
            corrected_name = correct_tool_name(collected_tc_name)
            collected_tc_name = corrected_name
            invalid_tool = validate_public_tool_name(corrected_name)
            if invalid_tool is not None:
                yield {"event": "error", "data": json.dumps({"error": invalid_tool.error})}
                yield {
                    "event": "escalation_triggered",
                    "data": json.dumps(
                        {
                            "reason": "unsupported_public_tool",
                            "tool": collected_tc_name,
                            "message": invalid_tool.error,
                        },
                        ensure_ascii=False,
                    ),
                }
                return
            try:
                state.tool_calls_used = enforce_tool_call_budget(collected_tc_name, state.tool_calls_used)
            except Exception as e:
                yield {"event": "error", "data": json.dumps({"error": str(e)})}
                return
            yield {
                "event": "tool_running",
                "data": json.dumps(build_tool_running_payload(collected_tc_name, args), ensure_ascii=False),
            }
            yield {
                "event": "phase_changed",
                "data": json.dumps({"phase": "tool_running", "tool": collected_tc_name}, ensure_ascii=False),
            }
            tool_failed = False
            tool_error_msg = ""
            try:
                if collected_tc_name in ("workspace_read_file", "workspace_write_file", "workspace_shell") and not args.get("project"):
                    args["project"] = project
                if collected_tc_name in ("workspace_read_file", "workspace_write_file") and not args.get("path"):
                    inferred = _infer_recent_file_path(conv)
                    if inferred:
                        args["path"] = inferred
                # Intercept calls with missing required params before tool execution
                if collected_tc_name == "workspace_shell" and not args.get("command"):
                    from luxar.server.tool_execution import ToolExecutionEnvelope as _TE
                    result = _TE(
                        ok=False, tool=collected_tc_name,
                        data={"error": "Missing required parameter 'command'. Provide a shell command like 'type Core/Src/main.c' or 'dir'."},
                        error="", summary_source={}, truncated=False,
                    )
                elif collected_tc_name in ("workspace_read_file", "workspace_write_file") and not args.get("path"):
                    from luxar.server.tool_execution import ToolExecutionEnvelope as _TE
                    result = _TE(
                        ok=False, tool=collected_tc_name,
                        data={"error": "Missing required parameter 'path'. Provide a relative file path like Core/Src/main.c."},
                        error="", summary_source={}, truncated=False,
                    )
                else:
                    result = await execute_tool_with_timeout(collected_tc_name, args, cfg, cm)
            except Exception as e:
                tool_failed = True
                tool_error_msg = str(e)
                from luxar.server.tool_execution import ToolExecutionEnvelope
                result = ToolExecutionEnvelope(
                    ok=False, tool=collected_tc_name,
                    data={"error": tool_error_msg},
                    error=tool_error_msg,
                    summary_source={}, truncated=False,
                )

            # Always append tool result BEFORE any early return, to keep conversation valid
            append_tool_result_message(
                api_messages,
                conv,
                tool_call_id=collected_tc_id,
                serialized_content=serialize_tool_content_for_llm(result, max_chars=16000 if collected_tc_name == "workspace_shell" else 40000 if collected_tc_name == "analyze_document_engineering" else 3000),
            )

            if tool_failed:
                yield {"event": "error", "data": json.dumps({"error": tool_error_msg})}
                yield {
                    "event": "escalation_triggered",
                    "data": json.dumps({"reason": "tool_timeout", "tool": collected_tc_name}, ensure_ascii=False),
                }
                return

            # Guard: detect same-tool-with-same-args infinite loop
            loop_error = check_same_call_loop(state, collected_tc_name, args)
            if loop_error:
                yield {"event": "error", "data": json.dumps({"error": loop_error})}
                yield {
                    "event": "escalation_triggered",
                    "data": json.dumps(
                        {"reason": "same_tool_loop", "tool": collected_tc_name, "count": state.same_call_streak},
                        ensure_ascii=False,
                    ),
                }
                return

            state.consecutive_failures, limit_message = update_consecutive_failures(
                state.consecutive_failures,
                result,
                is_failure=is_tool_result_failure,
                limit=max_consecutive_tool_failures,
                build_limit_message=build_consecutive_failure_limit_message,
            )
            if limit_message is not None:
                yield {"event": "error", "data": json.dumps({"error": limit_message})}
                yield {
                    "event": "escalation_triggered",
                    "data": json.dumps(
                        {"reason": "consecutive_tool_failures", "count": state.consecutive_failures},
                        ensure_ascii=False,
                    ),
                }
                return
            for event_payload in build_stream_tool_result_events(
                collected_tc_name,
                args,
                result,
                tool_call_id=collected_tc_id,
                serialize_tool_data=serialize_tool_data,
                format_summary=format_tool_result_summary,
            ):
                yield event_payload
            # Store verbose tool output for later refinement
            if collected_tc_name in ("workspace_shell", "workspace_read_file", "workspace_write_file") and result.ok:
                file_content = ""
                if collected_tc_name == "workspace_shell":
                    file_content = getattr(result, "data", {}).get("stdout", "")
                elif collected_tc_name == "workspace_read_file":
                    file_content = getattr(result, "data", {}).get("content", "")
                elif collected_tc_name == "workspace_write_file":
                    file_content = args.get("content", "")
                if file_content:
                    summary = f"{args.get('command', args.get('path', '?'))} ({len(file_content)} chars)"
                    if collected_tc_name == "workspace_write_file":
                        summary = f"Wrote {args.get('path', '?')} ({len(file_content)} chars)"
                    pending_outputs[collected_tc_id] = {
                        "summary": summary,
                        "file_content": file_content,
                    }
        else:
            # After LLM responds, refine tool outputs based on what LLM referenced
            for tc_id, pout in pending_outputs.items():
                refined = extract_referenced_portions(round_content, pout["file_content"])
                if refined:
                    yield {
                        "event": "tool_output_refine",
                        "data": json.dumps(
                            {"tool_call_id": tc_id, "summary": pout["summary"], "content": refined},
                            ensure_ascii=False,
                        ),
                    }
            pending_outputs.clear()
            final_content = round_content
            final_reasoning = round_reasoning
            break
    else:
        final_content += "\n\n_I've reached the maximum number of tool call rounds. Please ask me to continue if needed._"

    append_final_assistant_message(conv, content=final_content, reasoning_content=final_reasoning)
    save_conversation(project)
    yield {"event": "done", "data": "[DONE]"}
