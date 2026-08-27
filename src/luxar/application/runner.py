"""工作流运行边界：统一执行 Graph，并把能力异常转换成失败 State。

S3 起 Runner 同时负责 LangGraph interrupt() 的暂停与恢复：
run_workflow 返回 WorkflowRunResult（含 thread_id 与可选的待审批请求），
resume_workflow 用 Command(resume=...) 在同一 checkpoint 上继续执行。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, cast

from langgraph.types import Command

from luxar.application.context import RuntimeContext
from luxar.application.graph import build_graph
from luxar.application.nodes import failed
from luxar.application.state import WorkflowState
from luxar.domain.devices import ApprovalRequest
from luxar.domain.interactions import WorkflowDecision, WorkflowInteraction
from luxar.domain.errors import WorkflowError
from luxar.ports.errors import CapabilityError
from luxar.ports.espidf_errors import EspIdfError
from luxar.ports.workspace_errors import WorkspaceError


# 将底层能力错误分类翻译成工作流能够保存的业务分类。
CAPABILITY_CATEGORY_MAP = {
    "authentication": "authentication",
    "timeout": "timeout",
    "rate_limit": "rate_limit",
    "service": "service",
    "empty_response": "model_output",
    "invalid_json": "model_output",
    "invalid_schema": "model_output",
}


# 这些文字完全由应用控制，不使用异常原始消息。
# 即使底层异常意外包含密钥或服务器响应，也不会进入 State。
WORKFLOW_ERROR_MESSAGES = {
    "authentication": "模型服务认证失败",
    "timeout": "模型服务请求超时",
    "rate_limit": "模型服务请求受到限流",
    "service": "模型服务暂时不可用",
    "model_output": "模型返回的数据不符合工作流要求",
}


WORKFLOW_ERROR_SUGGESTIONS = {
    "authentication": "请检查模型服务的 API 密钥配置",
    "timeout": "请稍后重试或检查网络连接",
    "rate_limit": "请等待限流恢复后重试",
    "service": "请稍后重试并检查模型服务状态",
    "model_output": "请重试；如果持续失败，请检查模型和提示词配置",
}


WORKSPACE_ERROR_MESSAGES = {
    "invalid_project": "项目目录或修复目标无效",
    "unsafe_path": "工作区拒绝了不安全的文件路径",
    "unsupported_file": "修复计划包含不允许修改的文件",
    "file_too_large": "项目文件超过工作区处理上限",
    "context_too_large": "项目源码总量超过工作区处理上限",
    "invalid_encoding": "项目源码不是有效的 UTF-8 文本",
    "io": "工作区文件操作失败",
    "rollback_failed": "工作区修复失败且未能完整回滚",
}


WORKSPACE_ERROR_SUGGESTIONS = {
    "invalid_project": "请检查项目目录和修复目标是否存在",
    "unsafe_path": "请检查修复计划中的项目相对路径",
    "unsupported_file": "请仅修改允许的 ESP-IDF 项目源码",
    "file_too_large": "请缩小单个源码文件或调整工作区限制",
    "context_too_large": "请缩小本次修复涉及的源码范围",
    "invalid_encoding": "请将项目源码转换为 UTF-8 文本",
    "io": "请检查文件权限后重试",
    "rollback_failed": "请停止自动修复并人工检查工作区文件",
}


ESPIDF_ERROR_MESSAGES = {
    "invalid_project": "ESP-IDF 项目结构无效",
    "environment": "ESP-IDF 构建环境不可用",
    "dependency": "项目依赖需要显式授权后才能解析",
    "process": "ESP-IDF 构建进程无法启动",
    "serial": "串口不可用或未配置",
}


ESPIDF_ERROR_SUGGESTIONS = {
    "invalid_project": "请检查项目根目录和 CMakeLists.txt",
    "environment": "请在已激活的 ESP-IDF 环境中重试",
    "dependency": "请确认依赖来源后显式允许依赖下载",
    "process": "请检查 ESP-IDF 命令、权限和运行环境",
    "serial": "请通过 --port 指定开发板串口并确认设备已连接",
}


ProgressStage = Literal[
    "requirement",
    "analysis",
    "planning",
    "project",
    "implementation",
    "build",
    "flash",
    "monitor",
    "diagnosis",
    "repair",
    "clarification",
    "completed",
    "failed",
]


@dataclass(frozen=True)
class WorkflowProgress:
    """Report safe progress plus user-facing narration for incremental UI."""

    stage: ProgressStage
    message: str
    attempts: int
    narrative: str = ""


@dataclass(frozen=True)
class PdfWorkflowProgress:
    stage: ProgressStage
    message: str
    attempts: int
    progress_type: str
    current: int
    total: int
    unit: str
    phase: str
    batch: int
    narrative: str = ""


ProgressReporter = Callable[[WorkflowProgress | PdfWorkflowProgress], None]


_PROGRESS_BY_NODE: dict[
    str,
    tuple[ProgressStage, str],
] = {
    "analyze_requirement": ("requirement", "需求分析完成"),
    "analyze_project": ("analysis", "当前项目代码分析完成"),
    "report_project": ("completed", "项目检查完成"),
    "create_plan": ("planning", "执行计划已生成"),
    "create_project": ("project", "项目框架已创建"),
    "find_idf_examples": ("analysis", "ESP-IDF 官方例程检索完成"),
    "implement_change": ("implementation", "已根据代码现状实现需求"),
    "repair_project": ("repair", "已应用受限制的源码修复"),
    "monitor_project": ("monitor", "已采集设备运行日志"),
    "analyze_device_logs": ("diagnosis", "设备日志分析完成"),
    "request_clarification": ("clarification", "需要补充需求信息"),
    "completed": ("completed", "工作流执行成功"),
    "failed": ("failed", "工作流执行失败"),
}


_STEP_LABELS = {
    "create_project": "创建项目框架",
    "implement_change": "根据现有代码实现需求",
    "build_project": "构建并验证固件",
    "flash_project": "烧录固件",
    "monitor_project": "采集并分析设备日志",
}


def _changed_file_text(state: WorkflowState) -> str:
    paths = list(dict.fromkeys(state.get("changed_files", [])))
    if not paths:
        return "没有记录到文件写入"
    shown = "、".join(paths[:8])
    if len(paths) > 8:
        shown += f" 等 {len(paths)} 个文件"
    return shown


def _narrative_from_state(state: WorkflowState, node: str) -> str:
    """Build bounded prose from validated state without exposing raw logs."""

    if node == "analyze_requirement":
        # 需求解析是内部动作，不单独冒充一个角色向用户汇报。
        return ""

    if node == "create_plan":
        plan = state.get("plan")
        labels = [
            _STEP_LABELS[step.kind]
            for step in plan.steps
            if step.kind in _STEP_LABELS
        ] if plan is not None else []
        if labels:
            prefix = "处理方案"
            if "implement_change" not in [step.kind for step in plan.steps]:
                prefix += "（本次不写入源码）"
            return "结合当前源码，我整理了执行顺序：" + " → ".join(labels) + "。\n\n"
        return "处理方案已生成，将按依赖顺序执行。\n\n"

    if node == "analyze_project":
        analysis = state.get("project_analysis")
        if analysis is None:
            return "正在读取当前项目代码并建立可验证的项目快照。\n\n"
        if not analysis.project_exists:
            return "我检查了项目位置：目录尚不存在，计划会先创建基础工程，再重新读取源码。\n\n"
        cache_note = "（源码未变化，复用已有分析）" if analysis.cache_hit else ""
        return f"我检查了当前源码{cache_note}：{analysis.summary}\n\n"

    if node == "create_project":
        created = state.get("created_project")
        if created is not None and created.success:
            return "项目写入：基础工程结构已创建，随后将重新读取源码并生成计划。\n\n"
        return "项目框架创建没有成功，我正在整理可操作的失败原因。\n\n"

    if node == "implement_change":
        return (
            f"代码写入：{_changed_file_text(state)}。"
            "写入后的项目快照已刷新，下一步执行构建验证。\n\n"
        )

    if node == "find_idf_examples":
        references = state.get("reference_examples", [])
        if references:
            paths = "、".join(item.path for item in references)
            retrieval = (
                "SDK 知识库召回并经本地文件确认"
                if any("sdk-rag" in item.matched_terms for item in references)
                else "本地关键词检索确认"
            )
            return (
                f"实现前，我通过{retrieval}找到了与需求匹配的 "
                f"ESP-IDF 官方例程 {paths}。"
                "实现时将优先沿用其中兼容的 API 和初始化方式。\n\n"
            )
        return (
            "实现前，我在当前 ESP-IDF 安装中没有找到足够匹配的官方例程，"
            "本次将根据现有项目结构实现，并由构建结果验证。\n\n"
        )

    if node == "report_project":
        return ""

    if node == "build_project":
        attempts = state.get("attempts", 0)
        evidence = state.get("build_evidence")
        if evidence is not None and evidence.success:
            return (
                f"构建验证：第 {attempts} 次构建通过，"
                f"工具返回码为 {evidence.return_code}，固件产物已生成。\n\n"
            )
        return (
            f"第 {attempts} 次构建发现了问题。"
            "我会根据受控的编译诊断判断是否可以自动修复。\n\n"
        )

    if node == "repair_project":
        return (
            f"构建修复：已更新 {_changed_file_text(state)}。"
            "接下来重新构建，以工具结果判断修复是否有效。\n\n"
        )

    if node == "flash_project":
        evidence = state.get("flash_evidence")
        if evidence is not None and evidence.success:
            return "固件已经成功写入开发板，接下来可以检查设备实际运行状态。\n\n"
        return "本次烧录没有成功，我正在判断是否可以安全重试。\n\n"

    if node == "monitor_project":
        return "设备运行日志已经采集完成，我正在检查启动、异常和循环重启等信号。\n\n"

    if node == "analyze_device_logs":
        diagnosis = state.get("device_diagnosis")
        if diagnosis is not None and diagnosis.healthy:
            return "日志分析完成，没有发现需要修复的运行故障。\n\n"
        findings = len(diagnosis.findings) if diagnosis is not None else 0
        return f"日志分析发现 {findings} 项运行问题，我会按诊断结果继续处理。\n\n"

    if node == "request_clarification":
        return "当前需求还缺少会直接影响实现的关键信息，我需要先向你确认。\n\n"
    if node == "completed":
        return ""
    if node == "failed":
        return ""
    return ""


def _progress_from_state(state: WorkflowState) -> WorkflowProgress | None:
    trace = state.get("trace", [])
    if not trace:
        return None

    node = trace[-1]
    attempts = state.get("attempts", 0)
    if node == "build_project":
        return WorkflowProgress(
            stage="build",
            message=f"已完成第 {attempts} 次构建",
            attempts=attempts,
            narrative=_narrative_from_state(state, node),
        )

    if node == "flash_project":
        flash_attempts = state.get("flash_attempts", 0)
        return WorkflowProgress(
            stage="flash",
            message=f"已完成第 {flash_attempts} 次烧录",
            attempts=attempts,
            narrative=_narrative_from_state(state, node),
        )

    configured = _PROGRESS_BY_NODE.get(node)
    if configured is None:
        return None

    stage, message = configured
    return WorkflowProgress(
        stage=stage,
        message=message,
        attempts=attempts,
        narrative=_narrative_from_state(state, node),
    )


def capability_error_to_workflow_error(
    error: CapabilityError,
    state: WorkflowState,
) -> WorkflowError:
    # 根据已经成功写入 State 的数据，判断失败发生在哪个模型阶段。
    if state.get("task_mode") == "inspection":
        stage = "project_analysis"
    elif "requirement" not in state:
        stage = "requirement_analysis"
    elif "project_analysis" not in state:
        stage = "project_analysis"
    elif "plan" not in state:
        stage = "planning"
    elif state.get("pending_step_kind") == "implement_change":
        stage = "implementation"
    else:
        stage = "repair"

    category = CAPABILITY_CATEGORY_MAP[error.category]

    # model_validate 接收普通字典，并由 Pydantic 验证最终领域对象。
    return WorkflowError.model_validate(
        {
            "stage": stage,
            "category": category,
            "message": WORKFLOW_ERROR_MESSAGES[category],
            "retryable": error.retryable,
            "user_suggestion": WORKFLOW_ERROR_SUGGESTIONS[category],
        }
    )


def workspace_error_to_workflow_error(
    error: WorkspaceError,
    state: WorkflowState | None = None,
) -> WorkflowError:
    stage = (
        "project_analysis"
        if state is not None and "project_analysis" not in state
        else "repair"
    )
    return WorkflowError.model_validate(
        {
            "stage": stage,
            "category": "workspace",
            "message": WORKSPACE_ERROR_MESSAGES[
                error.category
            ],
            "retryable": error.retryable,
            "user_suggestion": WORKSPACE_ERROR_SUGGESTIONS[
                error.category
            ],
        }
    )


def espidf_error_to_workflow_error(
    error: EspIdfError,
    state: WorkflowState,
) -> WorkflowError:
    if error.category == "dependency":
        category = "dependency"
    elif error.category == "serial":
        category = "serial"
    else:
        category = "environment"

    # 分发器在执行节点前写入 pending_step_kind，因此它可以
    # 精确指出失败发生在计划执行链的哪个阶段。
    pending = state.get("pending_step_kind")
    if pending == "create_project":
        stage = "project_creation"
    elif pending == "flash_project":
        stage = "flash"
    elif pending == "monitor_project":
        stage = "monitor"
    else:
        stage = "build"

    return WorkflowError.model_validate(
        {
            "stage": stage,
            "category": category,
            "message": ESPIDF_ERROR_MESSAGES[error.category],
            "retryable": error.retryable,
            "user_suggestion": ESPIDF_ERROR_SUGGESTIONS[
                error.category
            ],
        }
    )


@dataclass(frozen=True)
class WorkflowRunResult:
    """一次运行的结果：终态 State、thread_id，以及可选的待审批请求。"""

    state: WorkflowState
    thread_id: str
    pending_approval: ApprovalRequest | WorkflowInteraction | None = None


ApprovalHandler = Callable[[ApprovalRequest | WorkflowInteraction], bool | WorkflowDecision]


def _parse_interaction(payload: object) -> ApprovalRequest | WorkflowInteraction:
    if isinstance(payload, dict) and payload.get("kind") in {
        "plan_review", "clarification", "knowledge_write", "repair_review"
    }:
        return WorkflowInteraction.model_validate(payload)
    return ApprovalRequest.model_validate(payload)


def _normalize_capability_failure(
    error: CapabilityError | WorkspaceError | EspIdfError,
    latest_state: WorkflowState,
) -> WorkflowError:
    if isinstance(error, CapabilityError):
        return capability_error_to_workflow_error(error, latest_state)
    if isinstance(error, WorkspaceError):
        return workspace_error_to_workflow_error(error, latest_state)
    return espidf_error_to_workflow_error(error, latest_state)


def _report_progress(
    progress_reporter: ProgressReporter | None,
    state: WorkflowState,
) -> None:
    if progress_reporter is None:
        return

    progress = _progress_from_state(state)
    if progress is not None:
        progress_reporter(progress)


def _drive_graph(
    graph_input: object,
    *,
    context: RuntimeContext,
    thread_id: str,
    progress_reporter: ProgressReporter | None,
    approval_handler: ApprovalHandler | None,
    latest_state: WorkflowState,
) -> WorkflowRunResult:
    # interrupt() 需要带 checkpointer 编译的 Graph；context.checkpointer
    # 由 Bootstrap 注入（正式运行使用 SqliteSaver，测试可回退
    # InMemorySaver）。
    runtime_context = context
    if progress_reporter is not None:
        runtime_context = replace(
            context,
            pdf_progress_reporter=lambda progress: progress_reporter(
                PdfWorkflowProgress(
                    stage="analysis",
                    message=progress.message,
                    attempts=0,
                    progress_type="pdf",
                    current=progress.completed_pages,
                    total=progress.total_pages,
                    unit="pages",
                    phase=progress.phase,
                    batch=progress.batch_number,
                )
            ),
        )
    graph = build_graph(checkpointer=runtime_context.checkpointer)
    config = {"configurable": {"thread_id": thread_id}}

    snapshots = iter(
        graph.stream(
            graph_input,
            context=runtime_context,
            config=config,
            stream_mode="values",
        )
    )
    last_trace_length = len(latest_state.get("trace", []))

    while True:
        try:
            snapshot = next(snapshots)
        except StopIteration:
            break
        # 整个工作流只有这一处统一捕获三种能力异常。
        except (CapabilityError, WorkspaceError, EspIdfError) as error:
            workflow_error = _normalize_capability_failure(
                error,
                latest_state,
            )
            failure_update = failed(latest_state)
            latest_state = cast(
                WorkflowState,
                {
                    **latest_state,
                    "error": workflow_error,
                    **failure_update,
                },
            )
            _report_progress(progress_reporter, latest_state)
            return WorkflowRunResult(
                state=latest_state,
                thread_id=thread_id,
            )

        # 快照中出现 __interrupt__ 表示 Graph 在审批节点暂停。
        # 该键是 LangGraph 内部数据，必须从业务 State 中剥离。
        if "__interrupt__" in snapshot:
            interrupt_payload = snapshot["__interrupt__"][0].value
            request = _parse_interaction(interrupt_payload)
            business_state = cast(
                WorkflowState,
                {
                    key: value
                    for key, value in snapshot.items()
                    if key != "__interrupt__"
                },
            )
            paused_state = cast(
                WorkflowState,
                {
                    **business_state,
                    "approval_status": "pending",
                    **(
                        {"approval_request": request}
                        if isinstance(request, ApprovalRequest)
                        else {"interaction": request}
                    ),
                },
            )

            # 没有展示层审批回调时，把审批请求原样交还调用方。
            if approval_handler is None:
                return WorkflowRunResult(
                    state=paused_state,
                    thread_id=thread_id,
                    pending_approval=request,
                )

            # 有回调时立即决策并继续同一次运行。
            handler_result = approval_handler(request)
            decision = (
                handler_result
                if isinstance(handler_result, WorkflowDecision)
                else WorkflowDecision(approved=bool(handler_result))
            )
            return resume_workflow(
                thread_id=thread_id,
                context=context,
                approved=decision.approved,
                feedback=decision.feedback,
                selected_option=decision.selected_option,
                progress_reporter=progress_reporter,
                approval_handler=approval_handler,
            )

        latest_state = cast(WorkflowState, snapshot)
        trace_length = len(latest_state.get("trace", []))
        if trace_length <= last_trace_length:
            continue
        last_trace_length = trace_length

        _report_progress(progress_reporter, latest_state)

    return WorkflowRunResult(
        state=latest_state,
        thread_id=thread_id,
    )


def run_workflow(
    *,
    initial_state: WorkflowState,
    context: RuntimeContext,
    progress_reporter: ProgressReporter | None = None,
    approval_handler: ApprovalHandler | None = None,
    thread_id: str | None = None,
) -> WorkflowRunResult:
    # 先复制初始 State。即使第一个节点立刻失败，task_text 也不会丢失。
    latest_state = cast(
        WorkflowState,
        dict(initial_state),
    )
    resolved_thread_id = thread_id or uuid.uuid4().hex

    return _drive_graph(
        initial_state,
        context=context,
        thread_id=resolved_thread_id,
        progress_reporter=progress_reporter,
        approval_handler=approval_handler,
        latest_state=latest_state,
    )


def resume_workflow(
    *,
    thread_id: str,
    context: RuntimeContext,
    approved: bool,
    feedback: str = "",
    selected_option: str | None = None,
    progress_reporter: ProgressReporter | None = None,
    approval_handler: ApprovalHandler | None = None,
) -> WorkflowRunResult:
    # 恢复前先读取 checkpoint 里的最新业务 State，保证恢复期间
    # 出现的异常也能映射出正确阶段并保留已有证据。
    graph = build_graph(checkpointer=context.checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    checkpoint_values = graph.get_state(config).values or {}
    latest_state = cast(
        WorkflowState,
        dict(checkpoint_values),
    )

    return _drive_graph(
        Command(resume=WorkflowDecision(
            approved=bool(approved),
            feedback=feedback,
            selected_option=selected_option,
        ).model_dump(mode="json")),
        context=context,
        thread_id=thread_id,
        progress_reporter=progress_reporter,
        approval_handler=approval_handler,
        latest_state=latest_state,
    )
