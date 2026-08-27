"""Supervisor Graph 的正式运行边界。"""

from __future__ import annotations

import html
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from luxar.application.agent_graph import build_agent_graph
from luxar.application.agent_persistence import save_agent_snapshot
from luxar.application.agent_state import (
    AgentRuntimeContext,
    AgentState,
    SupervisorDecision,
)
from luxar.checkpoint_serde import create_checkpoint_serializer
from luxar.database.persistence import PersistencePort
from luxar.domain.agent.approvals import AgentApprovalRequest
from luxar.domain.agent.tasks import AgentTask, AgentTaskGraph
from luxar.domain.agent.verification import VerificationPlan
from luxar.domain.interactions import WorkflowDecision
from luxar.ports.errors import CapabilityError
from luxar.ports.espidf_errors import EspIdfError
from luxar.ports.workspace_errors import WorkspaceError


@dataclass(frozen=True)
class AgentWorkflowProgress:
    node: str
    message: str
    step_count: int
    phase: str = "completed"
    narrative: str = ""
    detail: str = ""
    tools: tuple[str, ...] = ()
    task_id: str | None = None


@dataclass(frozen=True)
class AgentWorkflowRunResult:
    state: AgentState
    thread_id: str
    pending_approval: AgentApprovalRequest | None = None
    checkpointer: BaseCheckpointSaver | None = None


AgentProgressReporter = Callable[[AgentWorkflowProgress], None]


_PROGRESS_MESSAGES = {
    "load_project_session": "已加载项目级目标和恢复状态",
    "supervisor": "Supervisor 已决定下一步动作",
    "project_inspector": "已检查工程结构和现有能力",
    "hardware_validator": "已验证硬件资源约束",
    "architecture_planner": "已生成分层任务图",
    "task_executor": "已执行一个就绪任务",
    "acceptance_verifier": "已依据工具证据检查验收条件",
    "answer_user": "需要用户补充信息",
    "complete_objective": "项目目标已完成",
    "degrade_capability": "项目目标已阻塞并保留当前状态",
    "fail_objective": "项目目标执行失败",
    "runner_error": "工作流在安全边界内终止",
}

_ACTION_LABELS = {
    "inspect_project": "检查项目结构和已有能力",
    "update_project_model": "更新项目模型",
    "plan_tasks": "生成分层任务图",
    "revise_plan": "根据证据修订任务图",
    "validate_hardware": "验证硬件资源约束",
    "execute_task": "执行下一个就绪任务",
    "verify_acceptance": "核对验收条件和工具证据",
    "answer_user": "回答用户问题",
    "ask_user": "请求用户补充信息",
    "request_approval": "请求高风险操作审批",
    "degrade_capability": "保留现场并报告阻塞",
    "complete_objective": "完成项目目标",
    "fail_objective": "终止失败目标",
}

_ACTION_TOOLS: dict[str, tuple[str, ...]] = {
    "inspect_project": ("workspace.read_project_files", "project.inspect"),
    "update_project_model": ("project.inspect",),
    "plan_tasks": ("agent.plan_tasks",),
    "revise_plan": ("agent.revise_plan",),
    "validate_hardware": ("hardware.validate",),
    "verify_acceptance": ("acceptance.verify",),
}

_NODE_TOOLS: dict[str, tuple[str, ...]] = {
    "load_project_session": ("project.session.load",),
    "project_inspector": ("workspace.read_project_files", "project.inspect"),
    "hardware_validator": ("hardware.validate",),
    "architecture_planner": ("agent.plan_tasks",),
    "acceptance_verifier": ("acceptance.verify",),
}

_TASK_KIND_TOOLS: dict[str, tuple[str, ...]] = {
    "inspect_project": ("project.inspect",),
    "architecture_plan": ("agent.plan_tasks",),
    "code_change": ("code_engineer.create_bundle", "code_executor.apply"),
    "verify_acceptance": ("verification.run",),
}

_TOOL_LABELS = {
    "project.session.load": "恢复项目会话",
    "workspace.read": "读取工程文件",
    "workspace.read_project_files": "读取工程文件",
    "project.inspect": "分析项目结构与已有能力",
    "project.plan": "规划项目变更边界",
    "agent.plan_tasks": "生成任务计划",
    "agent.revise_plan": "修订任务计划",
    "hardware.validate": "校验硬件资源约束",
    "workspace.patch": "修改允许范围内的工程文件",
    "source.validate": "校验源码和非回归约束",
    "code_engineer.create_bundle": "生成受限代码变更",
    "code_executor.apply": "事务式应用代码变更",
    "source.assert": "检查源码断言",
    "acceptance.verify": "核对验收条件",
    "verification.run": "执行验证计划",
    "component.test": "运行组件测试",
    "espidf.build": "构建 ESP-IDF 工程",
    "device.flash": "烧录开发板固件",
    "firmware.inspect": "检查固件资源",
    "device.monitor": "采集设备日志",
    "protocol.probe": "执行协议探测",
    "runtime.scenario": "执行运行时场景",
}


def _safe_progress_text(value: object, *, limit: int = 600) -> str:
    text = " ".join(str(value or "").split())
    bounded = text if len(text) <= limit else f"{text[:limit]}…"
    return html.escape(bounded, quote=True)


def _safe_tool_name(value: object) -> str:
    raw = "".join(
        character if character.isalnum() or character in "._:-" else "_"
        for character in str(value or "")
    )
    return raw.strip("_")[:80]


def _decision(state: AgentState) -> SupervisorDecision | None:
    raw = state.get("decision")
    if raw is None:
        return None
    try:
        return (
            raw
            if isinstance(raw, SupervisorDecision)
            else SupervisorDecision.model_validate(raw)
        )
    except ValidationError:
        return None


def _task_graph(state: AgentState) -> AgentTaskGraph | None:
    raw = state.get("task_graph")
    if raw is None:
        return None
    try:
        return (
            raw
            if isinstance(raw, AgentTaskGraph)
            else AgentTaskGraph.model_validate(raw)
        )
    except ValidationError:
        return None


def _progress_task(state: AgentState, *, prefer_ready: bool) -> AgentTask | None:
    graph = _task_graph(state)
    if graph is None:
        return None
    if prefer_ready:
        ready = graph.ready_tasks()
        if ready:
            return ready[0]
    current_id = state.get("current_task_id")
    if current_id:
        current = next(
            (task for task in graph.tasks if task.task_id == current_id),
            None,
        )
        if current is not None:
            return current
    return None


def _task_tools(task: AgentTask | None) -> tuple[str, ...]:
    if task is None:
        return ()
    names = (
        _safe_tool_name(tool)
        for tool in [
            *task.allowed_tools,
            *_TASK_KIND_TOOLS.get(task.kind, ()),
        ]
    )
    return tuple(
        dict.fromkeys(name for name in names if name)
    )


def _tool_text(tool: str) -> str:
    label = _TOOL_LABELS.get(tool, "调用受控工具")
    return f"{label}（`{tool}`）"


def _tools_text(tools: tuple[str, ...]) -> str:
    return "、".join(_tool_text(tool) for tool in tools)


def _value_field(value: object, field: str, default: object = None) -> object:
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _plan_narrative(state: AgentState, step_count: int) -> str:
    graph = _task_graph(state)
    if graph is None:
        return f"**第 {step_count} 轮｜执行计划生成失败**\n\n"
    objective = state.get("objective")
    objective_title = _safe_progress_text(
        _value_field(objective, "title", "当前项目目标"),
        limit=240,
    )
    lines = [
        f"**第 {step_count} 轮｜执行计划已生成**",
        f"目标：{objective_title}",
        f"计划共 {len(graph.tasks)} 步，将按依赖顺序执行：",
    ]
    shown_tasks = graph.tasks[:20]
    for index, task in enumerate(shown_tasks, 1):
        approval = "；执行前需要你的明确批准" if task.requires_approval else ""
        lines.append(
            f"{index}. **{_safe_progress_text(task.title, limit=240)}**："
            f"{_safe_progress_text(task.description, limit=500)}{approval}"
        )
        tools = _task_tools(task)
        if tools:
            lines.append(f"   将使用：{_tools_text(tools)}")
        if task.allowed_paths:
            paths = "、".join(
                f"`{_safe_progress_text(path, limit=240)}`"
                for path in task.allowed_paths[:12]
            )
            lines.append(f"   文件范围：{paths}")
        if task.preserves:
            preserves = "、".join(
                _safe_progress_text(item, limit=160)
                for item in task.preserves[:12]
            )
            lines.append(f"   必须保持：{preserves}")
    if len(graph.tasks) > len(shown_tasks):
        lines.append(f"其余 {len(graph.tasks) - len(shown_tasks)} 个任务已保留在任务图中。")
    if graph.acceptance_criteria:
        lines.append("完成前将逐项验证：")
        for criterion in graph.acceptance_criteria[:20]:
            lines.append(
                "- "
                + _safe_progress_text(criterion.description, limit=500)
            )
        if len(graph.acceptance_criteria) > 20:
            lines.append(
                f"- 其余 {len(graph.acceptance_criteria) - 20} 项验收条件"
            )
    return "\n\n".join(lines) + "\n\n"


def _node_result_lines(
    state: AgentState,
    node: str,
    task: AgentTask | None,
) -> list[str]:
    if node == "load_project_session":
        file_count = len(state.get("project_files", []))
        project = _safe_progress_text(
            state.get("project_name", "当前工程"),
            limit=240,
        )
        return [f"已载入项目“{project}”，当前读取到 {file_count} 个受控源码文件。"]
    if node == "project_inspector":
        capabilities = state.get("capabilities", [])
        shown = [
            _safe_progress_text(
                _value_field(item, "capability_id", "未知能力"),
                limit=200,
            )
            for item in capabilities[:12]
        ]
        result = [f"从源码中识别出 {len(capabilities)} 项已有能力。"]
        if shown:
            result.append("已有能力包括：" + "、".join(shown))
        return result
    if node == "hardware_validator":
        report = state.get("hardware_report")
        issues = list(_value_field(report, "issues", []) or [])
        assignments = list(_value_field(report, "assignments", []) or [])
        if state.get("hardware_blocked"):
            messages = [
                _safe_progress_text(
                    _value_field(issue, "message", "硬件约束不满足"),
                    limit=400,
                )
                for issue in issues[:8]
            ]
            return [
                "硬件校验未通过：" + "；".join(messages)
                if messages
                else "硬件校验未通过，已停止后续修改。"
            ]
        return [
            f"硬件约束校验通过；确认 {len(assignments)} 项资源分配，"
            f"发现 {len(issues)} 项非阻塞提示。"
        ]
    if node == "architecture_planner":
        return []
    if node == "task_executor" and task is not None:
        result = [
            f"任务“{_safe_progress_text(task.title, limit=240)}”当前状态为 "
            f"{task.status}，已尝试 {task.attempts}/{task.max_attempts} 次。"
        ]
        validation = state.get("change_validations", {}).get(task.task_id)
        changed_files = list(
            _value_field(validation, "changed_files", []) or []
        )
        if changed_files:
            result.append(
                "实际修改文件："
                + "、".join(
                    f"`{_safe_progress_text(path, limit=240)}`"
                    for path in changed_files[:20]
                )
            )
        if task.kind == "verify_acceptance":
            result.append(
                "验证结果："
                + (
                    "构建证据通过"
                    if state.get("build_verified")
                    else "尚未取得构建通过证据"
                )
                + "；"
                + (
                    "设备功能证据通过"
                    if state.get("hardware_function_verified")
                    else "本任务未取得或未要求设备功能证据"
                )
                + "。"
            )
        return result
    if node == "acceptance_verifier":
        criteria = state.get("acceptance_criteria", [])
        passed = sum(
            _value_field(item, "status", "pending") == "passed"
            for item in criteria
        )
        pending = [
            _safe_progress_text(
                _value_field(item, "description", "未命名验收条件"),
                limit=400,
            )
            for item in criteria
            if _value_field(item, "status", "pending") != "passed"
        ]
        result = [f"验收进度：{passed}/{len(criteria)} 项通过。"]
        if pending:
            result.append("尚未通过：" + "；".join(pending[:10]))
        return result
    if node == "complete_objective":
        return ["所有强制任务和验收条件已经取得工具证据，目标可以完成。"]
    if node == "answer_user":
        message = _safe_progress_text(state.get("last_error", ""))
        return [message] if message else []
    return []


def _progress_for_node(state: AgentState, node: str) -> AgentWorkflowProgress:
    step_count = state.get("step_count", 0)
    if node == "supervisor":
        decision = _decision(state)
        action = decision.action if decision is not None else ""
        label = _ACTION_LABELS.get(action, "选择下一步动作")
        task = _progress_task(
            state,
            prefer_ready=action == "execute_task",
        )
        tools = (
            _task_tools(task)
            if action == "execute_task"
            else _ACTION_TOOLS.get(action, ())
        )
        rationale = _safe_progress_text(
            decision.rationale if decision is not None else ""
        )
        lines = [f"**第 {step_count} 轮｜Supervisor 决策：{label}**"]
        if rationale:
            lines.append(rationale)
        if task is not None and action == "execute_task":
            lines.append(f"当前任务：{_safe_progress_text(task.title, limit=240)}")
        if tools:
            lines.append("准备调用：" + _tools_text(tools))
        return AgentWorkflowProgress(
            node=node,
            message=f"下一步：{label}",
            step_count=step_count,
            phase="decision",
            narrative="\n\n".join(lines) + "\n\n",
            detail=rationale,
            tools=tools,
            task_id=task.task_id if task is not None else None,
        )

    task = _progress_task(state, prefer_ready=False) if node == "task_executor" else None
    tools = _task_tools(task) if task is not None else _NODE_TOOLS.get(node, ())
    message = _PROGRESS_MESSAGES.get(node, f"已完成步骤：{node}")
    if node == "architecture_planner":
        graph = _task_graph(state)
        return AgentWorkflowProgress(
            node=node,
            message=message,
            step_count=step_count,
            phase="completed",
            narrative=_plan_narrative(state, step_count),
            detail=(
                f"已生成 {len(graph.tasks)} 个任务，按依赖顺序执行。"
                if graph is not None
                else "执行计划生成失败。"
            ),
            tools=tools,
        )
    lines = [f"**第 {step_count} 轮｜{message}**"]
    result_lines = _node_result_lines(state, node, task)
    lines.extend(result_lines)
    if tools:
        lines.append("本步调用完成：" + _tools_text(tools))
    last_error = _safe_progress_text(state.get("last_error", ""))
    if node in {"degrade_capability", "fail_objective", "runner_error"} and last_error:
        lines.append(f"结果：{last_error}")
    return AgentWorkflowProgress(
        node=node,
        message=message,
        step_count=step_count,
        phase="completed",
        narrative="\n\n".join(lines) + "\n\n",
        detail="；".join(result_lines),
        tools=tools,
        task_id=task.task_id if task is not None else None,
    )


def _failed_state(
    initial_state: AgentState,
    *,
    message: str,
) -> AgentState:
    return {
        **initial_state,
        "status": "failed",
        "last_error": message,
        "trace": [*initial_state.get("trace", []), "runner_error"],
    }


def _prepare_initial_state(
    initial_state: AgentState,
    context: AgentRuntimeContext,
) -> AgentState:
    prepared: AgentState = dict(initial_state)  # type: ignore[assignment]
    prepared.setdefault("trace", [])
    prepared.setdefault("max_steps", 40)
    task_text = prepared.get("task_text", "").casefold()
    flash_requested = (
        any(marker in task_text for marker in ("烧录", "刷写", "flash"))
        and not any(
            marker in task_text
            for marker in ("不要烧录", "不烧录", "无需烧录", "do not flash")
        )
    )
    modification_requested = any(
        marker in task_text
        for marker in (
            "修改",
            "设置",
            "实现",
            "新增",
            "添加",
            "删除",
            "替换",
            "修复",
            "编写",
            "开发",
            "modify",
            "implement",
            "fix",
            "create",
        )
    )
    if flash_requested and not modification_requested:
        prepared.setdefault("workflow_action", "flash")
    device_requested = flash_requested or any(
        marker in task_text
        for marker in ("串口日志", "设备日志", "监控设备", "monitor")
    )
    prepared.setdefault(
        "verification_plan",
        VerificationPlan(
            require_build=True,
            require_flash=flash_requested,
            require_device=device_requested,
        ),
    )
    if context.project_path is not None:
        prepared.setdefault("project_name", context.project_path.name)
    if "project_files" not in prepared:
        if context.workspace is None or context.project_path is None:
            prepared["project_files"] = []
        elif context.project_path.exists():
            prepared["project_files"] = context.workspace.read_project_files(
                context.project_path
            )
        else:
            prepared["project_files"] = []
    return prepared


def _report_trace(
    state: AgentState,
    reporter: AgentProgressReporter | None,
    *,
    start_index: int = 0,
) -> int:
    trace = state.get("trace", [])
    if reporter is None:
        return len(trace)
    for node in trace[start_index:]:
        reporter(_progress_for_node(state, node))
    return len(trace)


def _save_state(
    state: AgentState,
    persistence: PersistencePort | None,
    project_key: str | None,
) -> None:
    if persistence is not None and project_key is not None:
        save_agent_snapshot(persistence, project_key, state)


def _drive_agent_graph(
    graph_input: object,
    *,
    context: AgentRuntimeContext,
    thread_id: str,
    checkpointer: BaseCheckpointSaver,
    latest_state: AgentState,
    persistence: PersistencePort | None,
    project_key: str | None,
    progress_reporter: AgentProgressReporter | None,
) -> AgentWorkflowRunResult:
    graph = build_agent_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    reported_trace_length = len(latest_state.get("trace", []))
    for snapshot in graph.stream(
        graph_input,
        config=config,
        context=context,
        stream_mode="values",
    ):
        if "__interrupt__" in snapshot:
            request = AgentApprovalRequest.model_validate(
                snapshot["__interrupt__"][0].value
            )
            latest_state = cast(
                AgentState,
                {
                    key: value
                    for key, value in snapshot.items()
                    if key != "__interrupt__"
                },
            )
            latest_state = cast(
                AgentState,
                {
                    **latest_state,
                    "status": "awaiting_user",
                    "approval_request": request,
                    "approval_status": "pending",
                },
            )
            _save_state(latest_state, persistence, project_key)
            _report_trace(
                latest_state,
                progress_reporter,
                start_index=reported_trace_length,
            )
            return AgentWorkflowRunResult(
                state=latest_state,
                thread_id=thread_id,
                pending_approval=request,
                checkpointer=checkpointer,
            )
        latest_state = cast(AgentState, snapshot)
        reported_trace_length = _report_trace(
            latest_state,
            progress_reporter,
            start_index=reported_trace_length,
        )

    _save_state(latest_state, persistence, project_key)
    return AgentWorkflowRunResult(
        state=latest_state,
        thread_id=thread_id,
        checkpointer=checkpointer,
    )


def _boundary_failure(
    initial_state: AgentState,
    error: Exception,
) -> AgentState:
    if isinstance(error, CapabilityError):
        message = "模型服务未能生成有效的项目级结构化结果"
    elif isinstance(error, WorkspaceError):
        message = "工程源码读取或受控写入失败"
    elif isinstance(error, EspIdfError):
        message = "ESP-IDF 工具执行失败"
    else:
        message = "Supervisor 状态或模型输出未通过结构验证"
    return _failed_state(initial_state, message=message)


def run_agent_workflow(
    *,
    initial_state: AgentState,
    context: AgentRuntimeContext,
    thread_id: str | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    persistence: PersistencePort | None = None,
    project_key: str | None = None,
    progress_reporter: AgentProgressReporter | None = None,
) -> AgentWorkflowRunResult:
    """运行 Supervisor，并只把受控错误消息写入可持久化 State。"""

    selected_thread_id = thread_id or uuid.uuid4().hex
    selected_checkpointer = checkpointer or InMemorySaver(
        serde=create_checkpoint_serializer()
    )
    try:
        prepared = _prepare_initial_state(initial_state, context)
        return _drive_agent_graph(
            prepared,
            context=context,
            thread_id=selected_thread_id,
            checkpointer=selected_checkpointer,
            latest_state=prepared,
            persistence=persistence,
            project_key=project_key,
            progress_reporter=progress_reporter,
        )
    except (
        CapabilityError,
        WorkspaceError,
        EspIdfError,
        ValidationError,
        ValueError,
        TypeError,
    ) as error:
        result = _boundary_failure(initial_state, error)
        _save_state(result, persistence, project_key)
    _report_trace(
        result,
        progress_reporter,
        start_index=len(initial_state.get("trace", [])),
    )
    return AgentWorkflowRunResult(
        state=result,
        thread_id=selected_thread_id,
        checkpointer=selected_checkpointer,
    )


def resume_agent_workflow(
    *,
    thread_id: str,
    context: AgentRuntimeContext,
    checkpointer: BaseCheckpointSaver,
    approved: bool,
    feedback: str = "",
    selected_option: str | None = None,
    persistence: PersistencePort | None = None,
    project_key: str | None = None,
    progress_reporter: AgentProgressReporter | None = None,
) -> AgentWorkflowRunResult:
    graph = build_agent_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    latest_state = cast(AgentState, dict(graph.get_state(config).values or {}))
    try:
        return _drive_agent_graph(
            Command(
                resume=WorkflowDecision(
                    approved=bool(approved),
                    feedback=feedback,
                    selected_option=selected_option,
                ).model_dump(mode="json")
            ),
            context=context,
            thread_id=thread_id,
            checkpointer=checkpointer,
            latest_state=latest_state,
            persistence=persistence,
            project_key=project_key,
            progress_reporter=progress_reporter,
        )
    except (
        CapabilityError,
        WorkspaceError,
        EspIdfError,
        ValidationError,
        ValueError,
        TypeError,
    ) as error:
        result = _boundary_failure(latest_state, error)
        _save_state(result, persistence, project_key)
        _report_trace(
            result,
            progress_reporter,
            start_index=len(latest_state.get("trace", [])),
        )
        return AgentWorkflowRunResult(
            state=result,
            thread_id=thread_id,
            checkpointer=checkpointer,
        )


__all__ = [
    "AgentProgressReporter",
    "AgentWorkflowProgress",
    "AgentWorkflowRunResult",
    "resume_agent_workflow",
    "run_agent_workflow",
]
