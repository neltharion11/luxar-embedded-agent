"""Execution and checkpoint recovery boundary for dedicated workflows."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, cast

from langgraph.types import Command

from luxar.application.specialized_context import SpecializedRuntimeContext
from luxar.application.specialized_graph import build_specialized_graph
from luxar.application.specialized_state import SpecializedWorkflowState
from luxar.application.workbench_persistence import (
    save_knowledge_workbench_snapshot,
)
from luxar.domain.errors import WorkflowError
from luxar.domain.interactions import WorkflowDecision, WorkflowInteraction
from luxar.ports.errors import CapabilityError
from luxar.ports.workspace_errors import WorkspaceError


@dataclass(frozen=True)
class SpecializedWorkflowProgress:
    stage: Literal["analysis", "knowledge", "completed", "failed"]
    message: str
    attempts: int = 0
    narrative: str = ""


@dataclass(frozen=True)
class PdfSpecializedWorkflowProgress:
    stage: Literal["knowledge"]
    message: str
    progress_type: str
    current: int
    total: int
    unit: str
    phase: str
    batch: int
    attempts: int = 0
    narrative: str = ""


SpecializedProgressReporter = Callable[
    [SpecializedWorkflowProgress | PdfSpecializedWorkflowProgress], None
]
SpecializedApprovalHandler = Callable[
    [WorkflowInteraction],
    bool | WorkflowDecision,
]


@dataclass(frozen=True)
class SpecializedWorkflowRunResult:
    state: SpecializedWorkflowState
    thread_id: str
    pending_approval: WorkflowInteraction | None = None


_PROGRESS = {
    "analyze_project": ("analysis", "当前项目代码分析完成"),
    "report_project": ("completed", "项目检查完成"),
    "analyze_knowledge_task": ("knowledge", "知识任务分析完成"),
    "review_knowledge_task": ("knowledge", "知识操作已审核"),
    "route_knowledge_action": ("knowledge", "知识操作路径已确定"),
    "retrieve_knowledge_evidence": ("knowledge", "相关具体知识检索完成"),
    "normalize_knowledge_evidence": ("knowledge", "知识证据去重整理完成"),
    "assess_evidence_sufficiency": ("knowledge", "知识证据覆盖度检查完成"),
    "expand_knowledge_query": ("knowledge", "知识检索范围已扩展"),
    "synthesize_grounded_answer": ("knowledge", "基于证据的答案已生成"),
    "verify_grounded_answer": ("knowledge", "答案证据验证完成"),
    "revise_grounded_answer": ("knowledge", "答案已按验证意见修订"),
    "execute_knowledge_task": ("knowledge", "知识操作执行完成"),
    "completed": ("completed", "专用工作流执行成功"),
    "failed": ("failed", "专用工作流执行失败"),
}

_NARRATIVE = {
    "analyze_project": (
        "**项目检查｜源码分析完成**\n\n"
        "已读取受控工程文件并提取项目结构、入口和已有能力。\n\n"
        "工具调用完成：`workspace.read_project_files`、`project.analyze`\n\n"
    ),
    "report_project": (
        "**项目检查｜报告已生成**\n\n"
        "已根据源码证据整理项目现状和缺失项。\n\n"
    ),
    "analyze_knowledge_task": (
        "**知识任务｜意图分析完成**\n\n"
        "已识别查询或写入动作；写入操作仍需显式审批。\n\n"
        "工具调用完成：`knowledge.parse_task`\n\n"
    ),
    "review_knowledge_task": (
        "**知识任务｜风险审核完成**\n\n"
        "已核对操作类型、数据范围和审批要求。\n\n"
    ),
    "route_knowledge_action": (
        "**知识任务｜执行路径已确定**\n\n"
        "问答任务将进入检索、证据整理、答案生成和验证流程。\n\n"
    ),
    "retrieve_knowledge_evidence": (
        "**知识任务｜具体知识检索完成**\n\n"
        "已检索与问题直接相关的知识单元。\n\n"
        "工具调用完成：`knowledge.search`\n\n"
    ),
    "normalize_knowledge_evidence": (
        "**知识任务｜证据整理完成**\n\n"
        "已去除重复知识并控制单一来源占比和上下文预算。\n\n"
        "工具调用完成：`knowledge.prepare_evidence`\n\n"
    ),
    "assess_evidence_sufficiency": (
        "**知识任务｜证据覆盖度检查完成**\n\n"
        "已检查当前知识能否直接、完整地支撑回答。\n\n"
        "工具调用完成：`knowledge.assess_evidence`\n\n"
    ),
    "expand_knowledge_query": (
        "**知识任务｜正在补充检索**\n\n"
        "当前覆盖不足，已补充实体别名、功能类别和限制条件。\n\n"
        "工具调用完成：`knowledge.expand_query`\n\n"
    ),
    "synthesize_grounded_answer": (
        "**知识任务｜答案生成完成**\n\n"
        "已仅根据整理后的具体知识生成自然语言答案。\n\n"
        "工具调用完成：`model.synthesize_grounded_answer`\n\n"
    ),
    "verify_grounded_answer": (
        "**知识任务｜答案验证完成**\n\n"
        "已核对引用编号、引脚或数值结论及证据对应关系。\n\n"
        "工具调用完成：`knowledge.verify_answer`\n\n"
    ),
    "revise_grounded_answer": (
        "**知识任务｜答案修订完成**\n\n"
        "已根据验证反馈补充引用或删除无依据结论。\n\n"
        "工具调用完成：`model.revise_grounded_answer`\n\n"
    ),
    "execute_knowledge_task": (
        "**知识任务｜操作执行完成**\n\n"
        "工具调用完成：`knowledge.execute`\n\n"
    ),
    "completed": "**专用工作流已完成**\n\n",
    "failed": "**专用工作流执行失败，已保留安全错误信息**\n\n",
}


def _failure(
    error: CapabilityError | WorkspaceError,
    state: SpecializedWorkflowState,
) -> WorkflowError:
    if isinstance(error, WorkspaceError):
        return WorkflowError.model_validate(
            {
                "stage": "project_analysis",
                "category": "workspace",
                "message": "项目工作区读取失败",
                "retryable": error.retryable,
                "user_suggestion": "请检查项目目录、编码和文件权限",
            }
        )
    category = {
        "empty_response": "model_output",
        "invalid_json": "model_output",
        "invalid_schema": "model_output",
    }.get(error.category, error.category)
    stage = (
        "project_analysis"
        if state.get("task_mode") == "inspection"
        else "planning"
    )
    message = {
        "authentication": "模型服务认证失败",
        "timeout": "模型服务请求超时",
        "rate_limit": "模型服务请求受到限流",
        "service": "知识或文档服务暂时不可用",
        "model_output": "模型返回的数据不符合专用工作流要求",
    }[category]
    return WorkflowError.model_validate(
        {
            "stage": stage,
            "category": category,
            "message": message,
            "retryable": error.retryable,
            "user_suggestion": "请检查服务配置后重试",
        }
    )


def _report(
    reporter: SpecializedProgressReporter | None,
    state: SpecializedWorkflowState,
) -> None:
    if reporter is None or not state.get("trace"):
        return
    configured = _PROGRESS.get(state["trace"][-1])
    if configured is None:
        return
    stage, message = configured
    node = state["trace"][-1]
    narrative = _NARRATIVE.get(node, "")
    if node == "retrieve_knowledge_evidence":
        narrative += (
            f"检索轮次：{state.get('retrieval_round', 1)}；"
            f"本轮候选知识：{len(state.get('retrieval_matches', []))} 条。\n\n"
        )
    elif node == "normalize_knowledge_evidence":
        narrative += (
            f"去重后保留：{len(state.get('knowledge_evidence', []))} 条有效证据。\n\n"
        )
    elif node == "assess_evidence_sufficiency":
        assessment = state.get("evidence_assessment", {})
        narrative += (
            "检查结果："
            + ("证据充足" if assessment.get("sufficient") else "证据不足")
            + f"；{assessment.get('reason', '')}。\n\n"
        )
    elif node == "verify_grounded_answer":
        verification = state.get("answer_verification", {})
        narrative += (
            "验证结果："
            + ("通过" if verification.get("passed") else "需要修订")
            + "。\n\n"
        )
    elif node == "execute_knowledge_task":
        result = state.get("knowledge_result", {})
        if result.get("knowledge_units") is not None:
            narrative += (
                f"已写入 {result.get('knowledge_units', 0)} 条具体知识；"
                f"页批次仅用于读取和来源定位。\n\n"
            )
    reporter(
        SpecializedWorkflowProgress(
            stage=stage,  # type: ignore[arg-type]
            message=message,
            narrative=narrative,
        )
    )


def _drive(
    graph_input: object,
    *,
    context: SpecializedRuntimeContext,
    thread_id: str,
    progress_reporter: SpecializedProgressReporter | None,
    approval_handler: SpecializedApprovalHandler | None,
    latest_state: SpecializedWorkflowState,
) -> SpecializedWorkflowRunResult:
    runtime_context = context
    if progress_reporter is not None:
        runtime_context = replace(
            context,
            pdf_progress_reporter=lambda progress: progress_reporter(
                PdfSpecializedWorkflowProgress(
                    stage="knowledge",
                    message=progress.message,
                    progress_type="pdf",
                    current=progress.completed_pages,
                    total=progress.total_pages,
                    unit="pages",
                    phase=progress.phase,
                    batch=progress.batch_number,
                )
            ),
        )
    graph = build_specialized_graph(checkpointer=runtime_context.checkpointer)
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
        except (CapabilityError, WorkspaceError) as error:
            latest_state = cast(
                SpecializedWorkflowState,
                {
                    **latest_state,
                    "error": _failure(error, latest_state),
                    "status": "failed",
                    "trace": [*latest_state.get("trace", []), "failed"],
                },
            )
            save_knowledge_workbench_snapshot(
                context.persistence,
                context.project_key,
                latest_state,
                thread_id=thread_id,
            )
            _report(progress_reporter, latest_state)
            return SpecializedWorkflowRunResult(
                state=latest_state,
                thread_id=thread_id,
            )

        if "__interrupt__" in snapshot:
            payload = snapshot["__interrupt__"][0].value
            interaction = WorkflowInteraction.model_validate(payload)
            paused = cast(
                SpecializedWorkflowState,
                {
                    **{
                        key: value
                        for key, value in snapshot.items()
                        if key != "__interrupt__"
                    },
                    "interaction": interaction,
                },
            )
            save_knowledge_workbench_snapshot(
                context.persistence,
                context.project_key,
                paused,
                thread_id=thread_id,
                awaiting_user=True,
            )
            if approval_handler is None:
                return SpecializedWorkflowRunResult(
                    state=paused,
                    thread_id=thread_id,
                    pending_approval=interaction,
                )
            handled = approval_handler(interaction)
            decision = (
                handled
                if isinstance(handled, WorkflowDecision)
                else WorkflowDecision(approved=bool(handled))
            )
            return resume_specialized_workflow(
                thread_id=thread_id,
                context=context,
                approved=decision.approved,
                feedback=decision.feedback,
                selected_option=decision.selected_option,
                progress_reporter=progress_reporter,
                approval_handler=approval_handler,
            )

        latest_state = cast(SpecializedWorkflowState, snapshot)
        save_knowledge_workbench_snapshot(
            context.persistence,
            context.project_key,
            latest_state,
            thread_id=thread_id,
        )
        trace_length = len(latest_state.get("trace", []))
        if trace_length <= last_trace_length:
            continue
        last_trace_length = trace_length
        _report(progress_reporter, latest_state)

    save_knowledge_workbench_snapshot(
        context.persistence,
        context.project_key,
        latest_state,
        thread_id=thread_id,
    )
    return SpecializedWorkflowRunResult(
        state=latest_state,
        thread_id=thread_id,
    )


def run_specialized_workflow(
    *,
    initial_state: SpecializedWorkflowState,
    context: SpecializedRuntimeContext,
    progress_reporter: SpecializedProgressReporter | None = None,
    approval_handler: SpecializedApprovalHandler | None = None,
    thread_id: str | None = None,
) -> SpecializedWorkflowRunResult:
    resolved_thread_id = thread_id or uuid.uuid4().hex
    return _drive(
        initial_state,
        context=context,
        thread_id=resolved_thread_id,
        progress_reporter=progress_reporter,
        approval_handler=approval_handler,
        latest_state=cast(SpecializedWorkflowState, dict(initial_state)),
    )


def resume_specialized_workflow(
    *,
    thread_id: str,
    context: SpecializedRuntimeContext,
    approved: bool,
    feedback: str = "",
    selected_option: str | None = None,
    progress_reporter: SpecializedProgressReporter | None = None,
    approval_handler: SpecializedApprovalHandler | None = None,
) -> SpecializedWorkflowRunResult:
    graph = build_specialized_graph(checkpointer=context.checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    latest_state = cast(
        SpecializedWorkflowState,
        dict(graph.get_state(config).values or {}),
    )
    decision = WorkflowDecision(
        approved=bool(approved),
        feedback=feedback,
        selected_option=selected_option,
    )
    return _drive(
        Command(resume=decision.model_dump(mode="json")),
        context=context,
        thread_id=thread_id,
        progress_reporter=progress_reporter,
        approval_handler=approval_handler,
        latest_state=latest_state,
    )


__all__ = [
    "PdfSpecializedWorkflowProgress",
    "SpecializedApprovalHandler",
    "SpecializedProgressReporter",
    "SpecializedWorkflowProgress",
    "SpecializedWorkflowRunResult",
    "resume_specialized_workflow",
    "run_specialized_workflow",
]
