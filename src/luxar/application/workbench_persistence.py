"""Persist bounded, workflow-neutral views for the Agent workbench."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

from luxar.application.specialized_state import SpecializedWorkflowState
from luxar.database.persistence import PersistencePort
from luxar.domain.errors import WorkflowError
from luxar.domain.interactions import WorkflowInteraction
from luxar.domain.knowledge_tasks import KnowledgeTask


_KNOWLEDGE_STEPS = (
    (
        "analyze_knowledge_task",
        "分析知识任务",
        "识别知识操作、输入来源和必要参数。",
        (),
    ),
    (
        "review_knowledge_task",
        "审核知识操作",
        "检查缺失信息；写操作必须等待用户明确批准。",
        ("analyze_knowledge_task",),
    ),
    (
        "route_knowledge_action",
        "确定执行路径",
        "区分知识问答、PDF 阅读和知识库写操作。",
        ("review_knowledge_task",),
    ),
)

_SEARCH_STEPS = (
    (
        "retrieve_knowledge_evidence",
        "检索具体知识",
        "检索与问题直接相关的知识原子，而不是 PDF 页块。",
        ("route_knowledge_action",),
    ),
    (
        "normalize_knowledge_evidence",
        "整理知识证据",
        "去重并控制来源多样性和证据上下文预算。",
        ("retrieve_knowledge_evidence",),
    ),
    (
        "assess_evidence_sufficiency",
        "检查证据覆盖度",
        "判断具体知识是否足以直接回答用户问题。",
        ("normalize_knowledge_evidence",),
    ),
    (
        "expand_knowledge_query",
        "补充检索范围",
        "证据不足时补充别名、功能类别和限制条件，最多两轮。",
        ("assess_evidence_sufficiency",),
    ),
    (
        "synthesize_grounded_answer",
        "生成证据约束答案",
        "只根据已整理的具体知识生成自然语言回答。",
        ("assess_evidence_sufficiency",),
    ),
    (
        "verify_grounded_answer",
        "验证答案和引用",
        "检查引用、引脚号、数值和限制条件是否有证据支持。",
        ("synthesize_grounded_answer",),
    ),
    (
        "revise_grounded_answer",
        "修订未通过的答案",
        "根据验证意见补充引用或删除无依据结论，最多两次。",
        ("verify_grounded_answer",),
    ),
)

_OPERATION_STEP = (
    "execute_knowledge_task",
    "执行知识操作",
    "调用受控知识或文档工具并保存结果证据。",
    ("route_knowledge_action",),
)

_LEGACY_OPERATION_STEP = (
    "execute_knowledge_task",
    "执行知识操作",
    "调用受控知识或文档工具并保存结果证据。",
    ("review_knowledge_task",),
)

_STEP_TOOLS: dict[str, list[str]] = {
    "analyze_knowledge_task": ["knowledge.parse_task"],
    "retrieve_knowledge_evidence": ["knowledge.search"],
    "normalize_knowledge_evidence": ["knowledge.prepare_evidence"],
    "assess_evidence_sufficiency": ["knowledge.assess_evidence"],
    "expand_knowledge_query": ["knowledge.expand_query"],
    "synthesize_grounded_answer": ["model.synthesize_grounded_answer"],
    "verify_grounded_answer": ["knowledge.verify_answer"],
    "revise_grounded_answer": ["model.revise_grounded_answer"],
    "execute_knowledge_task": [
        "knowledge.search",
        "document.read",
        "knowledge.write",
    ],
}


def _steps(task: KnowledgeTask | None) -> tuple[tuple[object, ...], ...]:
    if task is not None and task.action == "search":
        return (*_KNOWLEDGE_STEPS, *_SEARCH_STEPS)
    return (*_KNOWLEDGE_STEPS, _OPERATION_STEP)


def _model(
    value: object,
    model: type[BaseModel],
) -> BaseModel | None:
    if isinstance(value, model):
        return value
    if isinstance(value, Mapping):
        try:
            return model.model_validate(value)
        except ValidationError:
            return None
    return None


def _trimmed(value: object, *, limit: int = 2000) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _knowledge_task_view(task: KnowledgeTask | None) -> dict[str, object] | None:
    if task is None:
        return None
    return {
        "action": task.action,
        "summary": task.summary,
        "query": _trimmed(task.query),
        "source_uri": _trimmed(task.source_uri),
        "relative_path": _trimmed(task.relative_path),
        "file_path": _trimmed(task.file_path),
        "title": _trimmed(task.title),
        "document_id": _trimmed(task.document_id),
        "missing_fields": list(task.missing_fields),
        "mutating": task.mutating,
    }


def _knowledge_result_view(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, object] = {}
    for key in (
        "document_id",
        "chunks",
        "total_pages",
        "batches",
        "characters",
        "knowledge_units",
        "evidence_count",
        "retrieval_rounds",
        "deleted",
        "read_pdf",
    ):
        item = value.get(key)
        if item is None or isinstance(item, (str, int, float, bool)):
            if item is not None:
                result[key] = item
    for key in ("answer", "preview"):
        if value.get(key):
            result[key] = _trimmed(value[key])
    documents = value.get("documents")
    if isinstance(documents, list):
        result["document_count"] = len(documents)
        result["document_titles"] = [
            _trimmed(item.get("title", ""), limit=240)
            for item in documents[:20]
            if isinstance(item, Mapping) and item.get("title")
        ]
    matches = value.get("matches")
    if isinstance(matches, list):
        result["match_count"] = len(matches)
        result["match_titles"] = [
            _trimmed(item.get("title", ""), limit=240)
            for item in matches[:20]
            if isinstance(item, Mapping) and item.get("title")
        ]
    citations = value.get("citations")
    if isinstance(citations, list):
        result["citation_count"] = len(citations)
        result["citation_sources"] = [
            {
                "evidence_id": _trimmed(item.get("evidence_id", ""), limit=40),
                "title": _trimmed(item.get("title", ""), limit=240),
                "source_pages": list(item.get("source_pages", []))[:20]
                if isinstance(item.get("source_pages"), list)
                else [],
            }
            for item in citations[:20]
            if isinstance(item, Mapping)
        ]
    return result


def knowledge_workbench_snapshot(
    state: SpecializedWorkflowState,
    *,
    thread_id: str,
    awaiting_user: bool = False,
) -> dict[str, object]:
    """Build a bounded workbench view without storing document bodies."""

    task = _model(state.get("knowledge_task"), KnowledgeTask)
    knowledge_task = task if isinstance(task, KnowledgeTask) else None
    raw_error = state.get("error")
    error = _model(raw_error, WorkflowError)
    workflow_error = error if isinstance(error, WorkflowError) else None
    error_category = (
        workflow_error.category
        if workflow_error is not None
        else _trimmed(raw_error.get("category", "unknown"), limit=120)
        if isinstance(raw_error, Mapping)
        else "unknown"
    )
    error_message = (
        workflow_error.message
        if workflow_error is not None
        else _trimmed(raw_error.get("message", "知识任务执行失败"))
        if isinstance(raw_error, Mapping)
        else "知识任务执行失败"
    )
    interaction = _model(state.get("interaction"), WorkflowInteraction)
    workflow_interaction = (
        interaction if isinstance(interaction, WorkflowInteraction) else None
    )
    trace = list(state.get("trace", []))
    result = _knowledge_result_view(state.get("knowledge_result"))
    status = (
        "awaiting_user"
        if awaiting_user
        else str(state.get("status", "running"))
    )
    if status not in {"running", "awaiting_user", "completed", "failed"}:
        status = "running"

    # A persisted pending-approval run from before workbench snapshots existed
    # proves that analysis completed even when its parsed task is unavailable.
    analyzed = knowledge_task is not None or awaiting_user
    reviewed = "review_knowledge_task" in trace and not awaiting_user
    legacy_trace = (
        "execute_knowledge_task" in trace
        and "route_knowledge_action" not in trace
    )
    configured_steps = (
        (*_KNOWLEDGE_STEPS[:2], _LEGACY_OPERATION_STEP)
        if legacy_trace
        else _steps(knowledge_task)
    )
    step_statuses = {
        str(step_id): "passed" if step_id in trace else "pending"
        for step_id, *_ in configured_steps
    }
    expected: str | None = None
    if not analyzed:
        expected = "analyze_knowledge_task"
    elif awaiting_user or "review_knowledge_task" not in trace:
        expected = "review_knowledge_task"
    elif "route_knowledge_action" not in trace:
        expected = "route_knowledge_action"
    elif knowledge_task is not None and knowledge_task.action == "search":
        last = trace[-1] if trace else ""
        assessment = state.get("evidence_assessment", {})
        verification = state.get("answer_verification", {})
        expected = {
            "route_knowledge_action": "retrieve_knowledge_evidence",
            "retrieve_knowledge_evidence": "normalize_knowledge_evidence",
            "normalize_knowledge_evidence": "assess_evidence_sufficiency",
            "expand_knowledge_query": "retrieve_knowledge_evidence",
            "synthesize_grounded_answer": "verify_grounded_answer",
            "revise_grounded_answer": "verify_grounded_answer",
        }.get(last)
        if last == "assess_evidence_sufficiency":
            expected = (
                "synthesize_grounded_answer"
                if isinstance(assessment, Mapping) and assessment.get("sufficient")
                else "expand_knowledge_query"
            )
        elif last == "verify_grounded_answer":
            expected = (
                None
                if isinstance(verification, Mapping) and verification.get("passed")
                else "revise_grounded_answer"
            )
    else:
        expected = "execute_knowledge_task"
    if expected in step_statuses and status not in {"completed", "failed"}:
        step_statuses[expected] = "running" if awaiting_user else "ready"
    if status == "completed":
        for optional in ("expand_knowledge_query", "revise_grounded_answer"):
            if optional in step_statuses and optional not in trace:
                step_statuses[optional] = "cancelled"
    if status == "failed":
        failed_step = next(
            (
                step_id
                for step_id in reversed(trace)
                if step_id in step_statuses and step_id not in {"completed", "failed"}
            ),
            expected or "analyze_knowledge_task",
        )
        step_statuses[failed_step] = "failed"

    current_task_id = next(
        (
            step_id
            for step_id, *_ in configured_steps
            if step_statuses[str(step_id)] not in {"passed", "cancelled"}
        ),
        None,
    )
    evidence_id = f"knowledge-result:{thread_id}" if result is not None else None
    criterion_status = (
        "passed"
        if status == "completed" and result is not None
        else "failed"
        if status == "failed"
        else "pending"
    )
    title = _trimmed(
        (
            knowledge_task.summary
            if knowledge_task is not None
            else state.get("task_text")
        ),
        limit=240,
    ) or "知识任务"
    description = _trimmed(state.get("task_text"), limit=8000) or title

    interactions: list[dict[str, object]] = []
    if workflow_interaction is not None:
        interactions.append(
            {
                "interaction_id": f"knowledge:{thread_id}:approval",
                "objective_id": f"knowledge:{thread_id}",
                "kind": workflow_interaction.kind,
                "payload": {
                    "message": workflow_interaction.summary,
                    "title": workflow_interaction.title,
                },
                "queued": awaiting_user,
            }
        )

    return {
        "revision": 1,
        "status": status,
        "task_mode": "knowledge",
        "supports_interactions": False,
        "objective": {
            "objective_id": f"knowledge:{thread_id}",
            "title": title,
            "description": description,
            "status": (
                "completed"
                if status == "completed"
                else "blocked"
                if status == "failed"
                else "active"
            ),
            "priority": 50,
            "acceptance_criteria": [
                "知识问答必须生成直接答案并通过引用验证"
                if knowledge_task is not None and knowledge_task.action == "search"
                else "知识操作完成并产生结构化结果"
            ],
            "constraints": ["写操作必须经过明确审批"],
            "revision": 1,
        },
        "changes": [],
        "tasks": [
            {
                "task_id": step_id,
                "parent_id": None,
                "kind": "knowledge",
                "title": step_title,
                "description": description_text,
                "depends_on": list(depends_on),
                "status": step_statuses[step_id],
                "attempts": trace.count(step_id),
                "max_attempts": 2 if step_id in {
                    "retrieve_knowledge_evidence",
                    "expand_knowledge_query",
                    "verify_grounded_answer",
                    "revise_grounded_answer",
                } else 1,
                "requires_approval": (
                    step_id == "review_knowledge_task"
                    and bool(knowledge_task and knowledge_task.mutating)
                ),
                "allowed_tools": _STEP_TOOLS.get(step_id, []),
                "acceptance_criteria": (
                    ["答案引用存在且验证通过"]
                    if step_id == "verify_grounded_answer"
                    else ["知识操作完成并产生结构化结果"]
                    if step_id == "execute_knowledge_task"
                    else []
                ),
            }
            for step_id, step_title, description_text, depends_on in configured_steps
        ],
        "capabilities": [],
        "acceptance": [
            {
                "criterion_id": f"knowledge:{thread_id}:result",
                "description": (
                    "知识问答生成直接答案并通过引用验证"
                    if knowledge_task is not None and knowledge_task.action == "search"
                    else "知识操作完成并产生结构化结果"
                ),
                "verification_kind": "knowledge_result",
                "status": criterion_status,
                "required_evidence": [evidence_id] if evidence_id else [],
                "evidence_ids": [evidence_id] if evidence_id else [],
            }
        ],
        "evidence": (
            [
                {
                    "evidence_id": evidence_id,
                    "kind": "knowledge_result",
                    "accepted_by": [f"knowledge:{thread_id}:result"],
                }
            ]
            if evidence_id
            else []
        ),
        "interactions": interactions,
        "recovery": (
            [
                {
                    "task_id": current_task_id or "execute_knowledge_task",
                    "category": error_category,
                    "message": error_message,
                    "attempt": 1,
                    "repeated": False,
                }
            ]
            if raw_error is not None
            else []
        ),
        "current_task_id": current_task_id,
        "acceptance_passed": criterion_status == "passed",
        "build_verified": False,
        "hardware_function_verified": False,
        "blocked_reason": (
            error_message if raw_error is not None else None
        ),
        "knowledge_task": _knowledge_task_view(knowledge_task),
        "knowledge_result": result,
        "trace": trace,
    }


def save_knowledge_workbench_snapshot(
    persistence: PersistencePort | None,
    project_key: str | None,
    state: SpecializedWorkflowState,
    *,
    thread_id: str,
    awaiting_user: bool = False,
) -> None:
    if (
        persistence is None
        or project_key is None
        or state.get("task_mode") != "knowledge"
    ):
        return
    persistence.save_workbench_snapshot(
        project_key=project_key,
        workflow_family="knowledge_task",
        thread_id=thread_id,
        snapshot=knowledge_workbench_snapshot(
            state,
            thread_id=thread_id,
            awaiting_user=awaiting_user,
        ),
    )


__all__ = [
    "knowledge_workbench_snapshot",
    "save_knowledge_workbench_snapshot",
]
