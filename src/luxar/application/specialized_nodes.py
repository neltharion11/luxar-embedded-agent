"""Nodes owned by dedicated project-inspection and knowledge workflows."""

from __future__ import annotations

from pathlib import Path

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from luxar.application.project_analysis import (
    analyze_current_project,
    extract_focused_project_diagnosis,
    extract_focused_project_fact,
    render_project_analysis,
)
from luxar.application.specialized_context import SpecializedRuntimeContext
from luxar.application.specialized_state import SpecializedWorkflowState
from luxar.document_reader import PdfDocumentReader
from luxar.database.persistence import KnowledgeMatch
from luxar.domain.errors import WorkflowError
from luxar.domain.conversation import explicit_pdf_read_path
from luxar.domain.interactions import WorkflowDecision, WorkflowInteraction
from luxar.domain.knowledge_answers import (
    GroundedKnowledgeAnswer,
    KnowledgeEvidence,
)
from luxar.domain.knowledge_tasks import KnowledgeTask
from luxar.knowledge_answering import (
    EvidenceListKnowledgeAnswerer,
    assess_knowledge_evidence,
    expand_knowledge_query,
    prepare_knowledge_evidence,
    verify_grounded_answer,
)
from luxar.ports.errors import CapabilityError


def analyze_specialized_project(
    state: SpecializedWorkflowState,
    runtime: Runtime[SpecializedRuntimeContext],
) -> dict[str, object]:
    context = runtime.context
    project_files = context.workspace.read_project_files(context.project_path)
    analysis = analyze_current_project(
        project_path=context.project_path,
        target_chip=context.target_chip,
        workspace=context.workspace,
        analyzer=context.project_analyzer,
        persistence=context.persistence,
        project_key=context.project_key,
        inspection_request=state["task_text"],
    )
    decision = analysis.evidence_decision
    retrieval_selected = (
        decision.knowledge_retrieval == "retrieve"
        and context.knowledge_service is not None
    )
    retrieval_reason = decision.reason.strip()
    if decision.knowledge_retrieval == "retrieve" and context.knowledge_service is None:
        retrieval_reason = "项目知识库当前不可用，已限定为代码检查"
    return {
        "project_analysis": analysis,
        "knowledge_retrieval_selected": retrieval_selected,
        "knowledge_retrieval_reason": retrieval_reason,
        "knowledge_retrieval_query": (
            decision.knowledge_query.strip() or state["task_text"]
        ),
        "focused_response": (
            extract_focused_project_diagnosis(state["task_text"], project_files)
            or extract_focused_project_fact(state["task_text"], project_files)
            or ""
        ),
        "status": "running",
        "trace": [*state.get("trace", []), "analyze_project"],
    }


def report_specialized_project(
    state: SpecializedWorkflowState,
    runtime: Runtime[SpecializedRuntimeContext],
) -> dict[str, object]:
    focused_response = state.get("focused_response", "").strip()
    analysis = state["project_analysis"]
    reason = state.get("knowledge_retrieval_reason", "").strip()
    evidence_scope = (
        "证据范围：本次未检索知识库。"
        + (
            reason
            if reason
            else (
                analysis.evidence_decision.reason.strip()
                or "源码分析未发现必须由知识库补充的关键缺口。"
            )
        )
    )
    inspection = focused_response or render_project_analysis(
        runtime.context.project_path.name,
        analysis,
        state["task_text"],
    )
    return {
        "inspection_response": inspection + "\n\n" + evidence_scope,
        "status": "completed",
        "trace": [*state.get("trace", []), "report_project"],
    }


def prepare_project_knowledge_retrieval(
    state: SpecializedWorkflowState,
) -> dict[str, object]:
    """Turn a model-selected cross-source inspection into a read-only search."""

    reason = state.get("knowledge_retrieval_reason", "").strip()
    return {
        "knowledge_task": KnowledgeTask(
            action="search",
            summary="为当前代码问题检索补充证据",
            query=state.get("knowledge_retrieval_query", "").strip()
            or state["task_text"],
        ),
        "knowledge_retrieval_reason": reason,
        "trace": [*state.get("trace", []), "prepare_project_knowledge_retrieval"],
    }


def analyze_specialized_knowledge_task(
    state: SpecializedWorkflowState,
    runtime: Runtime[SpecializedRuntimeContext],
) -> dict[str, object]:
    pdf_path = explicit_pdf_read_path(state["task_text"])
    if pdf_path is not None:
        task = KnowledgeTask(
            action="read_pdf",
            summary="读取用户明确指定的本地 PDF",
            file_path=pdf_path,
            title=Path(pdf_path.replace("\\", "/")).stem,
        )
        return {
            "knowledge_task": task,
            "status": "running",
            "trace": [*state.get("trace", []), "analyze_knowledge_task"],
        }
    parser = runtime.context.knowledge_task_parser
    if parser is None:
        raise CapabilityError(
            category="service",
            message="未配置知识任务解析能力",
            retryable=False,
        )
    task = parser.parse(state["task_text"])
    return {
        "knowledge_task": task,
        "status": "running",
        "trace": [*state.get("trace", []), "analyze_knowledge_task"],
    }


def review_specialized_knowledge_task(
    state: SpecializedWorkflowState,
) -> dict[str, object]:
    task = state["knowledge_task"]
    interaction = WorkflowInteraction(
        kind="knowledge_write",
        title="确认知识库变更" if task.mutating else "知识库操作计划",
        summary=task.summary,
        questions=[f"请补充：{field}" for field in task.missing_fields],
        options=["批准执行", "补充或修改操作", "取消任务"],
        operation=task.model_dump(mode="json"),
    )
    trace = [*state.get("trace", []), "review_knowledge_task"]
    if not task.mutating and not task.missing_fields:
        return {
            "interaction": interaction,
            "interaction_action": "continue",
            "trace": trace,
        }

    raw = interrupt(interaction.model_dump(mode="json"))
    decision = WorkflowDecision.model_validate(
        raw if isinstance(raw, dict) else {}
    )
    feedback = " ".join(
        item
        for item in (decision.selected_option, decision.feedback.strip())
        if item
    ).strip()
    if decision.approved and not task.missing_fields and not feedback:
        return {
            "interaction": interaction,
            "interaction_action": "continue",
            "trace": trace,
        }
    if feedback:
        return {
            "task_text": state["task_text"] + "\n用户补充：" + feedback,
            "interaction": interaction,
            "interaction_action": "replan",
            "trace": trace,
        }
    return {
        "interaction": interaction,
        "interaction_action": "failed",
        "error": WorkflowError.model_validate(
            {
                "stage": "planning",
                "category": "approval_rejected",
                "message": "知识库变更未获批准，未执行任何写入",
                "retryable": False,
                "user_suggestion": "可修改操作后重新提交",
            }
        ),
        "trace": trace,
    }


def _resolve_document_path(
    *,
    raw_path: str,
    task_text: str,
    project_path: Path,
    authorization_message: str,
) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        if raw_path.casefold() not in task_text.casefold():
            raise CapabilityError(
                category="service",
                message=authorization_message,
                retryable=False,
            )
        return candidate.expanduser().resolve()

    root = project_path.resolve()
    source = (root / candidate).resolve()
    try:
        source.relative_to(root)
    except ValueError as error:
        raise CapabilityError(
            category="service",
            message="PDF 路径越出当前项目",
            retryable=False,
        ) from error
    return source


def execute_specialized_knowledge_task(
    state: SpecializedWorkflowState,
    runtime: Runtime[SpecializedRuntimeContext],
) -> dict[str, object]:
    task = state["knowledge_task"]
    context = runtime.context
    project_key = context.project_key or context.project_path.name
    raw_path = task.file_path.strip() or task.relative_path.strip()

    if task.action == "read_pdf":
        if not raw_path:
            raise CapabilityError(
                category="service",
                message="没有提供 PDF 路径",
                retryable=False,
            )
        source = _resolve_document_path(
            raw_path=raw_path,
            task_text=state["task_text"],
            project_path=context.project_path,
            authorization_message="模型给出的绝对路径并非用户明确授权的路径",
        )
        reader = context.document_reader or PdfDocumentReader()
        from luxar.document_reader import PdfReadProgress, iter_pdf_batches

        pdf_progress_reporter = getattr(context, "pdf_progress_reporter", None)
        batches = list(iter_pdf_batches(
            reader,
            source,
            progress_reporter=pdf_progress_reporter,
        ))
        content = "\n\n".join(batch.content for batch in batches)
        total_pages = batches[-1].total_pages if batches else 0
        if pdf_progress_reporter is not None and context.document_analyzer is not None:
            pdf_progress_reporter(PdfReadProgress(
                "analyzing",
                total_pages,
                total_pages,
                total_pages or None,
                len(batches),
                "页面读取完成，正在整理 PDF 技术信息",
            ))
        report = None
        analysis_warning = ""
        if context.document_analyzer is not None:
            try:
                report = context.document_analyzer.analyze(
                    task_text=state["task_text"],
                    title=task.title or source.stem,
                    batches=batches,
                    progress_reporter=pdf_progress_reporter,
                )
            except CapabilityError:
                # Reading is already complete. Preserve that successful local
                # work and return a bounded preview when model synthesis is
                # temporarily unavailable.
                analysis_warning = (
                    "PDF 全文已成功读取，但智能技术提炼服务暂不可用；"
                    "已降级返回原文预览，可稍后重试自动提炼。"
                )
        if report is not None and report.analysis_warnings:
            analysis_warning = "PDF 技术提炼部分降级：\n- " + "\n- ".join(
                report.analysis_warnings
            )
        if pdf_progress_reporter is not None:
            pdf_progress_reporter(PdfReadProgress(
                "completed",
                total_pages,
                total_pages,
                total_pages or None,
                len(batches),
                f"PDF 处理完成，共 {total_pages} 页",
            ))
        result: dict[str, object] = {
            "read_pdf": True,
            "title": task.title or source.stem,
            "total_pages": total_pages,
            "batches": len(batches),
            "sections": len(batches),
            "characters": len(content),
            "preview": content[:6000] if report is None else "",
        }
        if analysis_warning:
            result["analysis_warning"] = analysis_warning
            result["degraded"] = True
        if report is not None:
            result["answer"] = report.answer
            result["technical_context"] = report.technical_context
        return {
            "knowledge_result": result,
            "trace": [*state.get("trace", []), "execute_knowledge_task"],
        }

    service = context.knowledge_service
    if service is None:
        raise CapabilityError(
            category="service",
            message="外部知识库尚未配置",
            retryable=False,
        )
    if task.action == "list":
        result = {"documents": service.list_documents(project_key)}
    elif task.action == "search":
        result = {
            "matches": [
                item.__dict__
                for item in service.search(
                    project_key=project_key,
                    query=task.query,
                )
            ]
        }
    elif task.action == "delete":
        result = {
            "deleted": service.delete_document(
                project_key=project_key,
                document_id=task.document_id,
            ),
            "document_id": task.document_id,
        }
    elif task.action == "upsert":
        document = service.ingest(
            project_key=project_key,
            source_uri=task.source_uri,
            title=task.title,
            content=task.content,
            metadata={"learned_by": "agent"},
        )
        result = {
            "document_id": document.document_id,
            "chunks": document.chunks,
        }
    else:
        source = _resolve_document_path(
            raw_path=raw_path,
            task_text=state["task_text"],
            project_path=context.project_path,
            authorization_message="PDF 绝对路径未经用户明确授权",
        )
        imported = service.ingest_pdf(
            project_key=project_key,
            source_uri=raw_path.replace("\\", "/"),
            title=task.title or source.stem,
            path=source,
            reader=context.document_reader,
            extractor=context.knowledge_extractor,
            progress_reporter=getattr(context, "pdf_progress_reporter", None),
        )
        result = {
            "total_pages": imported.total_pages,
            "batches": imported.batches,
            "sections": imported.batches,
            "knowledge_units": imported.knowledge_units,
            "document_ids": [
                item.document_id for item in imported.documents
            ],
        }
    return {
        "knowledge_result": result,
        "trace": [*state.get("trace", []), "execute_knowledge_task"],
    }


def route_specialized_knowledge_action(
    state: SpecializedWorkflowState,
) -> dict[str, object]:
    return {
        "trace": [*state.get("trace", []), "route_knowledge_action"],
    }


def retrieve_specialized_knowledge_evidence(
    state: SpecializedWorkflowState,
    runtime: Runtime[SpecializedRuntimeContext],
) -> dict[str, object]:
    service = runtime.context.knowledge_service
    if service is None:
        raise CapabilityError(
            category="service",
            message="外部知识库尚未配置",
            retryable=False,
        )
    task = state["knowledge_task"]
    queries = list(state.get("knowledge_queries", []))
    query = queries[-1] if queries else task.query
    if not query.strip():
        query = state["task_text"]
    plan = state.get("response_plan", {})
    answer_budget = int(plan.get("answer_budget", 600))
    candidate_limit = max(8, min(18, answer_budget // 60))
    if plan.get("scope") == "broad":
        candidate_limit = 18
    matches = service.search(
        project_key=(
            runtime.context.project_key or runtime.context.project_path.name
        ),
        query=query,
        limit=candidate_limit,
    )
    if not queries:
        queries.append(query)
    return {
        "knowledge_queries": queries,
        "retrieval_matches": [item.__dict__ for item in matches],
        "retrieval_round": int(state.get("retrieval_round", 0)) + 1,
        "trace": [*state.get("trace", []), "retrieve_knowledge_evidence"],
    }


def normalize_specialized_knowledge_evidence(
    state: SpecializedWorkflowState,
) -> dict[str, object]:
    matches = [
        KnowledgeMatch(**item)
        for item in state.get("retrieval_matches", [])
        if isinstance(item, dict)
    ]
    analysis = state.get("project_analysis")
    if analysis is not None:
        matches.append(
            KnowledgeMatch(
                document_id="current-project-source",
                title="当前项目源码分析",
                source_uri="project://current-source",
                ordinal=0,
                content=render_project_analysis(
                    "当前项目",
                    analysis,
                    state.get("task_text"),
                ),
                score=1.0,
                subject="当前项目代码",
                category="project_code",
                limitations=("仅代表当前读取到的源码，不能替代实机证据",),
            )
        )
    for item in state.get("knowledge_evidence", []):
        if not isinstance(item, dict):
            continue
        prior = KnowledgeEvidence.model_validate(item)
        matches.append(
            KnowledgeMatch(
                document_id=prior.document_id,
                title=prior.title,
                source_uri=prior.source_uri,
                ordinal=0,
                content=prior.statement,
                score=prior.score,
                knowledge_id=prior.knowledge_id,
                subject=prior.subject,
                category=prior.category,
                source_pages=tuple(prior.source_pages),
                source_section=prior.source_section,
                applicable_conditions=tuple(prior.applicable_conditions),
                limitations=tuple(prior.limitations),
            )
        )
    plan = state.get("response_plan", {})
    answer_budget = int(plan.get("answer_budget", 600))
    evidence_limit = max(4, min(12, answer_budget // 120))
    if plan.get("scope") == "broad":
        evidence_limit = 12
    evidence = prepare_knowledge_evidence(matches, limit=evidence_limit)
    return {
        "knowledge_evidence": [item.model_dump(mode="json") for item in evidence],
        # Do not retain raw duplicate matches in checkpoints/workbench.
        "retrieval_matches": [],
        "trace": [*state.get("trace", []), "normalize_knowledge_evidence"],
    }


def assess_specialized_knowledge_evidence(
    state: SpecializedWorkflowState,
) -> dict[str, object]:
    evidence = [
        KnowledgeEvidence.model_validate(item)
        for item in state.get("knowledge_evidence", [])
    ]
    assessment = assess_knowledge_evidence(
        state["task_text"],
        evidence,
        retrieval_round=int(state.get("retrieval_round", 1)),
    )
    update: dict[str, object] = {
        "evidence_assessment": assessment.model_dump(mode="json"),
        "trace": [*state.get("trace", []), "assess_evidence_sufficiency"],
    }
    if not assessment.sufficient and int(state.get("retrieval_round", 1)) >= 2:
        update["error"] = WorkflowError.model_validate(
            {
                "stage": "planning",
                "category": "knowledge_insufficient",
                "message": "当前知识库没有足够的具体知识回答该问题",
                "retryable": False,
                "user_suggestion": "请导入相关技术资料或缩小问题范围",
            }
        )
    return update


def expand_specialized_knowledge_query(
    state: SpecializedWorkflowState,
) -> dict[str, object]:
    round_number = int(state.get("retrieval_round", 1)) + 1
    query = expand_knowledge_query(state["task_text"], round_number)
    queries = [*state.get("knowledge_queries", [])]
    if query not in queries:
        queries.append(query)
    return {
        "knowledge_queries": queries,
        "trace": [*state.get("trace", []), "expand_knowledge_query"],
    }


def synthesize_specialized_knowledge_answer(
    state: SpecializedWorkflowState,
    runtime: Runtime[SpecializedRuntimeContext],
) -> dict[str, object]:
    evidence = [
        KnowledgeEvidence.model_validate(item)
        for item in state.get("knowledge_evidence", [])
    ]
    answerer = runtime.context.knowledge_answerer or EvidenceListKnowledgeAnswerer()
    answer = answerer.answer(
        question=state["task_text"],
        evidence=evidence,
        response_plan=state.get("response_plan", {}),
        conversation_context=state.get("conversation_context", []),
    )
    return {
        "knowledge_answer": answer.model_dump(mode="json"),
        "answer_revision_count": 0,
        "trace": [*state.get("trace", []), "synthesize_grounded_answer"],
    }


def verify_specialized_knowledge_answer(
    state: SpecializedWorkflowState,
) -> dict[str, object]:
    evidence = [
        KnowledgeEvidence.model_validate(item)
        for item in state.get("knowledge_evidence", [])
    ]
    answer = GroundedKnowledgeAnswer.model_validate(state["knowledge_answer"])
    verification = verify_grounded_answer(answer, evidence)
    update: dict[str, object] = {
        "answer_verification": verification.model_dump(mode="json"),
        "trace": [*state.get("trace", []), "verify_grounded_answer"],
    }
    if not verification.passed and int(state.get("answer_revision_count", 0)) >= 2:
        update["error"] = WorkflowError.model_validate(
            {
                "stage": "planning",
                "category": "knowledge_answer_unverified",
                "message": "知识答案在重试边界内未通过证据验证",
                "retryable": False,
                "user_suggestion": "请补充更明确的知识来源后重试",
            }
        )
    if verification.passed:
        update["knowledge_result"] = {
            "answer": answer.answer_markdown,
            "citations": [
                {
                    "evidence_id": item.evidence_id,
                    "title": item.title,
                    "source_uri": item.source_uri,
                    "source_pages": item.source_pages,
                    "source_section": item.source_section,
                }
                for item in evidence
                if item.evidence_id in answer.cited_evidence_ids
                or f"[{item.evidence_id}]" in answer.answer_markdown
            ],
            "evidence_count": len(evidence),
            "retrieval_rounds": int(state.get("retrieval_round", 1)),
            "verification": verification.model_dump(mode="json"),
        }
    return update


def revise_specialized_knowledge_answer(
    state: SpecializedWorkflowState,
    runtime: Runtime[SpecializedRuntimeContext],
) -> dict[str, object]:
    evidence = [
        KnowledgeEvidence.model_validate(item)
        for item in state.get("knowledge_evidence", [])
    ]
    verification = state.get("answer_verification", {})
    instructions = str(verification.get("revision_instructions", ""))
    answerer = runtime.context.knowledge_answerer or EvidenceListKnowledgeAnswerer()
    answer = answerer.answer(
        question=state["task_text"],
        evidence=evidence,
        revision_instructions=instructions,
        response_plan=state.get("response_plan", {}),
        conversation_context=state.get("conversation_context", []),
    )
    return {
        "knowledge_answer": answer.model_dump(mode="json"),
        "answer_revision_count": int(state.get("answer_revision_count", 0)) + 1,
        "trace": [*state.get("trace", []), "revise_grounded_answer"],
    }


def complete_specialized_workflow(
    state: SpecializedWorkflowState,
) -> dict[str, object]:
    return {
        "status": "completed",
        "trace": [*state.get("trace", []), "completed"],
    }


def fail_specialized_workflow(
    state: SpecializedWorkflowState,
) -> dict[str, object]:
    return {
        "status": "failed",
        "trace": [*state.get("trace", []), "failed"],
    }


__all__ = [
    "analyze_specialized_knowledge_task",
    "analyze_specialized_project",
    "complete_specialized_workflow",
    "assess_specialized_knowledge_evidence",
    "expand_specialized_knowledge_query",
    "execute_specialized_knowledge_task",
    "fail_specialized_workflow",
    "normalize_specialized_knowledge_evidence",
    "prepare_project_knowledge_retrieval",
    "report_specialized_project",
    "retrieve_specialized_knowledge_evidence",
    "revise_specialized_knowledge_answer",
    "route_specialized_knowledge_action",
    "review_specialized_knowledge_task",
    "synthesize_specialized_knowledge_answer",
    "verify_specialized_knowledge_answer",
]
