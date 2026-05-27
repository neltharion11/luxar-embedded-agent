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
    update_consecutive_failures,
)


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
                invalid_tool = validate_public_tool_name(tc.function_name)
                if invalid_tool is not None:
                    return {
                        "content": invalid_tool.error or "Tool is not part of the public control plane.",
                        "reasoning_content": "",
                    }
                try:
                    if tc.function_name in ("workspace_read_file", "workspace_write_file") and not tc.arguments.get("project"):
                        tc.arguments["project"] = project
                    result, state.tool_calls_used = await execute_tool_with_limits(
                        tc.function_name,
                        tc.arguments,
                        cfg,
                        cm,
                        used_calls=state.tool_calls_used,
                    )
                except Exception as e:
                    return {
                        "content": str(e),
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
                append_tool_result_message(
                    api_messages,
                    conv,
                    tool_call_id=tc.id,
                    serialized_content=serialize_tool_content_for_llm(result),
                )
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
            invalid_tool = validate_public_tool_name(collected_tc_name)
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
            try:
                if collected_tc_name in ("workspace_read_file", "workspace_write_file") and not args.get("project"):
                    args["project"] = project
                result = await execute_tool_with_timeout(collected_tc_name, args, cfg, cm)
            except Exception as e:
                yield {"event": "error", "data": json.dumps({"error": str(e)})}
                yield {
                    "event": "escalation_triggered",
                    "data": json.dumps({"reason": "tool_timeout", "tool": collected_tc_name}, ensure_ascii=False),
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
            append_tool_result_message(
                api_messages,
                conv,
                tool_call_id=collected_tc_id,
                serialized_content=serialize_tool_content_for_llm(result),
            )
            for event_payload in build_stream_tool_result_events(
                collected_tc_name,
                args,
                result,
                serialize_tool_data=serialize_tool_data,
                format_summary=format_tool_result_summary,
            ):
                yield event_payload
        else:
            final_content = round_content
            final_reasoning = round_reasoning
            break
    else:
        final_content += "\n\n_I've reached the maximum number of tool call rounds. Please ask me to continue if needed._"

    append_final_assistant_message(conv, content=final_content, reasoning_content=final_reasoning)
    save_conversation(project)
    yield {"event": "done", "data": "[DONE]"}