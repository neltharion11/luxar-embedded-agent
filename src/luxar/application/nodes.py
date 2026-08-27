"""LangGraph 业务节点：通过 Runtime 中的 Ports 执行动作，并返回最小 State 更新。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from luxar.application.context import RuntimeContext
from luxar.application.project_analysis import (
    analyze_current_project,
    render_project_analysis,
)
from luxar.application.state import WorkflowState
from luxar.domain.devices import ApprovalRequest, FlashEvidence, MonitorEvidence
from luxar.domain.conversation import explicit_pdf_read_path
from luxar.domain.interactions import WorkflowDecision, WorkflowInteraction
from luxar.domain.knowledge_tasks import KnowledgeTask
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.errors import WorkflowError
from luxar.knowledge_answering import (
    EvidenceListKnowledgeAnswerer,
    prepare_knowledge_evidence,
    verify_grounded_answer,
)
from luxar.ports.errors import CapabilityError
from luxar.ports.espidf_errors import EspIdfError


# 已实现步骤词表随切片扩展；未进入本表的步骤会被分发器拒绝。
_SUPPORTED_STEP_KINDS = frozenset(
    {
        "create_project",
        "implement_change",
        "build_project",
        "flash_project",
        "monitor_project",
    }
)

_UNSUPPORTED_STEP_MESSAGE = "执行计划包含当前版本不支持的步骤"

_UNSUPPORTED_STEP_SUGGESTION = "请更换模型或升级 LUXAR 后重试"

_APPROVAL_SUMMARY = "即将向串口设备烧录固件，请确认目标芯片与串口"

_APPROVAL_REJECTED_MESSAGE = "烧录申请被用户拒绝"

_APPROVAL_REJECTED_SUGGESTION = "确认目标芯片和串口后重新运行任务"


def analyze_requirement(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    # runtime.context 由 LangGraph 在调用节点时注入；具体对象可以是 Fake 或 DeepSeek Adapter。
    requirement = runtime.context.requirement_parser.parse(
        state["task_text"]
    )
    # 项目创建/选择时固定的芯片是可信结构化上下文，不要求用户在每条
    # 自然语言需求中重复声明，也不允许模型用空值覆盖它。
    if runtime.context.target_chip:
        requirement = requirement.model_copy(
            update={
                "target": runtime.context.target_chip,
                "missing_fields": [
                    field
                    for field in requirement.missing_fields
                    if field != "target"
                ],
            }
        )

    # 节点返回局部更新而不是完整 State；LangGraph 会把这些键合并回当前状态。
    update: dict[str, object] = {
        "requirement": requirement,
        "status": "requirement_analyzed",
        "trace": [
            # * 展开旧列表，再追加当前节点名；不会原地修改传入的 trace。
            *state.get("trace", []),
            "analyze_requirement",
        ],
    }
    if runtime.context.interactive_workflow:
        update["interactive_workflow"] = True
    return update


def create_plan(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    requirement = state["requirement"]
    # State 提供业务输入，Runtime Context 提供完成动作所需的外部能力。
    planner = runtime.context.planner
    # 规划器必须看到刚刚验证过的项目快照，不能只凭用户一句话猜测现状。
    plan = planner.create_plan(
        requirement,
        project_analysis=state["project_analysis"],
    )
    analysis = state["project_analysis"]
    if not analysis.project_exists and not any(
        step.kind == "create_project" for step in plan.steps
    ):
        plan = ExecutionPlan(steps=[
            PlanStep(
                kind="create_project",
                description="创建最小 ESP-IDF 项目骨架并重新读取源码",
            ),
            *plan.steps,
        ])

    return {
        "plan": plan,
        "status": "planned",
        "trace": [
            *state.get("trace", []),
            "create_plan",
        ],
    }


def analyze_knowledge_task(
    state: WorkflowState, runtime: Runtime[RuntimeContext]
) -> dict[str, object]:
    pdf_path = explicit_pdf_read_path(state["task_text"])
    if pdf_path is not None:
        return {
            "knowledge_task": KnowledgeTask(
                action="read_pdf",
                summary="读取用户明确指定的本地 PDF",
                file_path=pdf_path,
                title=Path(pdf_path.replace("\\", "/")).stem,
            ),
            "trace": [*state.get("trace", []), "analyze_knowledge_task"],
        }
    parser = runtime.context.knowledge_task_parser
    if parser is None:
        raise CapabilityError(category="service", message="未配置知识任务解析能力", retryable=False)
    task = parser.parse(state["task_text"])
    return {
        "knowledge_task": task,
        "trace": [*state.get("trace", []), "analyze_knowledge_task"],
    }


def review_knowledge_task(state: WorkflowState) -> dict[str, object]:
    task = state["knowledge_task"]
    interaction = WorkflowInteraction(
        kind="knowledge_write",
        title="确认知识库变更" if task.mutating else "知识库操作计划",
        summary=task.summary,
        questions=[f"请补充：{field}" for field in task.missing_fields],
        options=["批准执行", "补充或修改操作", "取消任务"],
        operation=task.model_dump(mode="json"),
    )
    if not task.mutating and not task.missing_fields:
        return {
            "interaction": interaction,
            "interaction_action": "continue",
            "trace": [*state.get("trace", []), "review_knowledge_task"],
        }
    raw = interrupt(interaction.model_dump(mode="json"))
    decision = WorkflowDecision.model_validate(raw if isinstance(raw, dict) else {})
    feedback = " ".join(
        item for item in (decision.selected_option, decision.feedback.strip()) if item
    ).strip()
    trace = [*state.get("trace", []), "review_knowledge_task"]
    if decision.approved and not task.missing_fields and not feedback:
        return {"interaction": interaction, "interaction_action": "continue", "trace": trace}
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
        "error": WorkflowError.model_validate({
            "stage": "planning", "category": "approval_rejected",
            "message": "知识库变更未获批准，未执行任何写入",
            "retryable": False, "user_suggestion": "可修改操作后重新提交",
        }),
        "trace": trace,
    }


def execute_knowledge_task(
    state: WorkflowState, runtime: Runtime[RuntimeContext]
) -> dict[str, object]:
    task = state["knowledge_task"]
    project_key = runtime.context.project_key or runtime.context.project_path.name
    if task.action == "read_pdf":
        raw_path = task.file_path.strip() or task.relative_path.strip()
        if not raw_path:
            raise CapabilityError(
                category="service", message="没有提供 PDF 路径", retryable=False
            )
        candidate = Path(raw_path)
        if candidate.is_absolute():
            # 模型只能使用用户本轮明确写出的主机路径，不能自行探索其他位置。
            if raw_path.casefold() not in state["task_text"].casefold():
                raise CapabilityError(
                    category="service",
                    message="模型给出的绝对路径并非用户明确授权的路径",
                    retryable=False,
                )
            source = candidate.expanduser().resolve()
        else:
            root = runtime.context.project_path.resolve()
            source = (root / candidate).resolve()
            try:
                source.relative_to(root)
            except ValueError as error:
                raise CapabilityError(
                    category="service", message="PDF 路径越出当前项目", retryable=False
                ) from error
        reader = runtime.context.document_reader
        if reader is None:
            from luxar.document_reader import PdfDocumentReader

            reader = PdfDocumentReader()
        from luxar.document_reader import PdfReadProgress, iter_pdf_batches

        pdf_progress_reporter = getattr(
            runtime.context, "pdf_progress_reporter", None
        )
        batches = list(iter_pdf_batches(
            reader,
            source,
            progress_reporter=pdf_progress_reporter,
        ))
        content = "\n\n".join(batch.content for batch in batches)
        total_pages = batches[-1].total_pages if batches else 0
        analyzer = getattr(runtime.context, "document_analyzer", None)
        if pdf_progress_reporter is not None and analyzer is not None:
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
        if analyzer is not None:
            try:
                report = analyzer.analyze(
                    task_text=state["task_text"],
                    title=task.title or source.stem,
                    batches=batches,
                    progress_reporter=pdf_progress_reporter,
                )
            except CapabilityError:
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

    service = runtime.context.knowledge_service
    if service is None:
        raise CapabilityError(category="service", message="外部知识库尚未配置", retryable=False)
    if task.action == "list":
        result: dict[str, object] = {"documents": service.list_documents(project_key)}
    elif task.action == "search":
        evidence = prepare_knowledge_evidence(
            service.search(project_key=project_key, query=task.query, limit=18)
        )
        answerer = runtime.context.knowledge_answerer or EvidenceListKnowledgeAnswerer()
        answer = answerer.answer(
            question=state["task_text"],
            evidence=evidence,
        )
        verification = verify_grounded_answer(answer, evidence)
        if not verification.passed:
            raise CapabilityError(
                category="invalid_schema",
                message="知识答案未通过证据验证",
                retryable=True,
            )
        result = {
            "answer": answer.answer_markdown,
            "citations": [
                {
                    "evidence_id": item.evidence_id,
                    "title": item.title,
                    "source_uri": item.source_uri,
                    "source_pages": item.source_pages,
                }
                for item in evidence
                if item.evidence_id in answer.cited_evidence_ids
                or f"[{item.evidence_id}]" in answer.answer_markdown
            ],
            "evidence_count": len(evidence),
            "verification": verification.model_dump(mode="json"),
        }
    elif task.action == "delete":
        result = {"deleted": service.delete_document(
            project_key=project_key, document_id=task.document_id
        ), "document_id": task.document_id}
    elif task.action == "upsert":
        document = service.ingest(
            project_key=project_key, source_uri=task.source_uri,
            title=task.title, content=task.content,
            metadata={"learned_by": "agent"},
        )
        result = {"document_id": document.document_id, "chunks": document.chunks}
    else:
        raw_path = task.file_path.strip() or task.relative_path.strip()
        candidate = Path(raw_path)
        root = runtime.context.project_path.resolve()
        if candidate.is_absolute():
            if raw_path.casefold() not in state["task_text"].casefold():
                raise CapabilityError(
                    category="service", message="PDF 绝对路径未经用户明确授权", retryable=False
                )
            source = candidate.expanduser().resolve()
        else:
            source = (root / candidate).resolve()
            try:
                source.relative_to(root)
            except ValueError as error:
                raise CapabilityError(category="service", message="PDF 路径越出当前项目", retryable=False) from error
        imported = service.ingest_pdf(
            project_key=project_key,
            source_uri=raw_path.replace("\\", "/"),
            title=task.title or source.stem,
            path=source, reader=runtime.context.document_reader,
            extractor=runtime.context.knowledge_extractor,
            progress_reporter=getattr(
                runtime.context, "pdf_progress_reporter", None
            ),
        )
        result = {"total_pages": imported.total_pages, "batches": imported.batches,
                  "sections": imported.batches,
                  "knowledge_units": imported.knowledge_units,
                  "document_ids": [item.document_id for item in imported.documents]}
    return {
        "knowledge_result": result,
        "trace": [*state.get("trace", []), "execute_knowledge_task"],
    }


def propose_solution_learning(
    state: WorkflowState, runtime: Runtime[RuntimeContext]
) -> dict[str, object]:
    """成功实现后提出可复用经验，用户批准后才写入项目知识库。"""

    service = runtime.context.knowledge_service
    changed = list(dict.fromkeys(state.get("changed_files", [])))
    if service is None or not changed:
        return {
            "learning_result": {"skipped": True},
            "trace": [*state.get("trace", []), "propose_solution_learning"],
        }
    requirement = state.get("requirement")
    references = state.get("reference_examples", [])
    interaction = WorkflowInteraction(
        kind="knowledge_write",
        title="保存这次验证通过的实现经验",
        summary=(
            "代码已通过工作流验证。我可以把本次需求、实际修改文件和采用的 "
            "ESP-IDF 例程保存为项目知识，供下一次遇到相同设备或接口时优先检索复用。"
        ),
        options=["批准保存", "本次不保存"],
        operation={
            "action": "upsert",
            "changed_files": changed,
            "reference_examples": [item.path for item in references],
        },
        allow_feedback=False,
    )
    raw = interrupt(interaction.model_dump(mode="json"))
    decision = WorkflowDecision.model_validate(raw if isinstance(raw, dict) else {})
    trace = [*state.get("trace", []), "propose_solution_learning"]
    if not decision.approved:
        return {"learning_result": {"skipped": True}, "trace": trace}
    files = runtime.context.workspace.read_project_files(runtime.context.project_path)
    selected = [item for item in files if item.path in changed]
    requirement_json = (
        json.dumps(requirement.model_dump(mode="json"), ensure_ascii=False)
        if requirement is not None else "{}"
    )
    content = "\n".join([
        "# 已验证的固件实现经验",
        "## 需求", requirement_json,
        "## 采用的 ESP-IDF 官方例程",
        "\n".join(item.path for item in references) or "无",
        "## 最终源码",
        *[f"### {item.path}\n```\n{item.content}\n```" for item in selected],
    ])
    identity = hashlib.sha256(requirement_json.encode("utf-8")).hexdigest()[:20]
    document = service.ingest(
        project_key=runtime.context.project_key or runtime.context.project_path.name,
        source_uri=f"learned://firmware-solution/{identity}",
        title="已验证的固件实现：" + (requirement.goal if requirement else identity),
        content=content,
        metadata={"kind": "verified_firmware_solution", "changed_files": changed},
    )
    return {
        "learning_result": {"skipped": False, "document_id": document.document_id,
                            "chunks": document.chunks},
        "trace": trace,
    }


def review_plan(state: WorkflowState) -> dict[str, object]:
    """展示完整计划；任何项目或源码写入都位于此中断之后。"""

    plan = state["plan"]
    interaction = WorkflowInteraction(
        kind="plan_review",
        title="确认执行计划",
        summary=(
            "下面是完整计划。批准后我才会创建项目或修改源码；"
            "也可以直接补充配置或提出修改意见，我会据此重新规划。"
        ),
        plan=plan,
        questions=[
            item.question
            + (f"（默认：{item.default}）" if item.default else "")
            + (f"——{item.rationale}" if item.rationale else "")
            for item in plan.clarifications
        ],
        options=[
            *[option for item in plan.clarifications for option in item.options],
            "批准并开始执行", "补充要求后重新规划", "取消任务",
        ][:12],
    )
    raw = interrupt(interaction.model_dump(mode="json"))
    decision = WorkflowDecision.model_validate(raw if isinstance(raw, dict) else {})
    trace = [*state.get("trace", []), "review_plan"]
    feedback = " ".join(
        item for item in (decision.selected_option, decision.feedback.strip()) if item
    ).strip()
    if decision.approved and not feedback:
        return {
            "interaction": interaction,
            "interaction_action": "continue",
            "plan_approved": True,
            "status": "planned",
            "trace": trace,
        }
    if feedback:
        revisions = state.get("plan_revision_count", 0) + 1
        if revisions > 6:
            return {
                "interaction": interaction,
                "interaction_action": "failed",
                "error": WorkflowError.model_validate({
                    "stage": "planning",
                    "category": "model_output",
                    "message": "计划修改次数超过安全上限",
                    "retryable": False,
                    "user_suggestion": "请重新发起任务并一次说明核心要求",
                }),
                "trace": trace,
            }
        return {
            "task_text": state["task_text"] + "\n用户对计划的补充：" + feedback,
            "interaction": interaction,
            "interaction_action": "replan",
            "plan_revision_count": revisions,
            "plan_approved": False,
            "plan_index": 0,
            "trace": trace,
        }
    return {
        "interaction": interaction,
        "interaction_action": "failed",
        "error": WorkflowError.model_validate({
            "stage": "planning",
            "category": "approval_rejected",
            "message": "执行计划未获批准，项目未被修改",
            "retryable": False,
            "user_suggestion": "可以重新发起任务或提交计划修改建议",
        }),
        "trace": trace,
    }


def analyze_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    """Create or reuse the validated snapshot used by every later decision."""

    context = runtime.context
    analysis = analyze_current_project(
        project_path=context.project_path,
        target_chip=context.target_chip,
        workspace=context.workspace,
        analyzer=context.project_analyzer,
        persistence=context.persistence,
        project_key=context.project_key,
        inspection_request=(
            state["task_text"] if state.get("task_mode") == "inspection" else None
        ),
    )
    update: dict[str, object] = {
        "project_analysis": analysis,
        "status": "project_analyzed",
        "trace": [*state.get("trace", []), "analyze_project"],
        "interactive_workflow": context.interactive_workflow,
    }

    # A successful creator followed by a missing project is contradictory tool
    # evidence. Stop instead of planning against an imaginary workspace.
    created = state.get("created_project")
    if created is not None and created.success and not analysis.project_exists:
        update["error"] = WorkflowError.model_validate(
            {
                "stage": "planning",
                "category": "workspace",
                "message": "项目创建成功后仍无法读取项目目录",
                "retryable": False,
                "user_suggestion": "请检查项目目录权限和创建工具输出",
            }
        )
    return update


def report_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    """Render the same analysis object used by planning as human prose."""

    return {
        "inspection_response": render_project_analysis(
            runtime.context.project_path.name,
            state["project_analysis"],
            state.get("task_text"),
        ),
        "status": "completed",
        "trace": [*state.get("trace", []), "report_project"],
    }


def execute_next_step(
    state: WorkflowState,
) -> dict[str, object]:
    # 计划游标分发器：只负责读取已验证的计划并推进游标，不调用任何外部能力。
    plan = state["plan"]
    index = state.get("plan_index", 0)
    trace = [
        *state.get("trace", []),
        "execute_next_step",
    ]

    # 所有步骤都已执行完毕：清空待分发步骤，路由到 completed 终态节点。
    if index >= len(plan.steps):
        return {
            "pending_step_kind": None,
            "trace": trace,
        }

    # 分发前推进游标，因此步骤节点永远不需要管理 plan_index。
    step = plan.steps[index]
    update: dict[str, object] = {
        "plan_index": index + 1,
        "pending_step_kind": step.kind,
        "trace": trace,
    }

    # 词表已由领域模型验证；此处只拒绝当前版本尚未实现的步骤。
    if step.kind not in _SUPPORTED_STEP_KINDS:
        update["error"] = WorkflowError.model_validate(
            {
                "stage": "planning",
                "category": "model_output",
                "message": f"{_UNSUPPORTED_STEP_MESSAGE}：{step.kind}",
                "retryable": False,
                "user_suggestion": _UNSUPPORTED_STEP_SUGGESTION,
            }
        )

    return update


def create_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    # 项目只能创建在 Context 指定的父目录内；芯片优先采用显式配置。
    project_path = runtime.context.project_path
    target_chip = (
        runtime.context.target_chip
        or state["requirement"].target
    )
    evidence = runtime.context.project_creator.create_project(
        parent_dir=project_path.parent,
        project_name=project_path.name,
        target_chip=target_chip,
    )

    return {
        "created_project": evidence,
        "status": "project_created",
        "trace": [
            *state.get("trace", []),
            "create_project",
        ],
    }


def implement_change(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    """Implement the requested change from current code, then refresh its snapshot."""

    context = runtime.context
    editor = context.firmware_editor
    if editor is None:
        raise CapabilityError(
            category="service",
            message="未配置固件代码实现能力",
            retryable=False,
        )

    files = context.workspace.read_project_files(context.project_path)
    references = state.get("reference_examples", [])
    reference_files = []
    if context.example_library is not None:
        for reference in references:
            reference_files.extend(context.example_library.read(reference))
    change = editor.create_change(
        state["requirement"],
        state["project_analysis"],
        files,
        references,
        reference_files,
    )
    changed_now = context.workspace.apply_repair(
        context.project_path,
        change,
    )
    refreshed = analyze_current_project(
        project_path=context.project_path,
        target_chip=context.target_chip,
        workspace=context.workspace,
        analyzer=context.project_analyzer,
        persistence=context.persistence,
        project_key=context.project_key,
        force=True,
    )

    return {
        "implementation_plan": change,
        "changed_files": [
            *state.get("changed_files", []),
            *changed_now,
        ],
        "project_analysis": refreshed,
        "status": "implemented",
        "knowledge_available": context.knowledge_service is not None,
        "trace": [*state.get("trace", []), "implement_change"],
    }


def find_idf_examples(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    """Select official SDK examples before the first requirement-driven edit."""

    library = runtime.context.example_library
    references = (
        library.search(state["requirement"], limit=2)
        if library is not None
        else []
    )
    return {
        "reference_examples": references,
        "trace": [*state.get("trace", []), "find_idf_examples"],
    }


def request_flash_approval(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    trace = [
        *state.get("trace", []),
        "request_flash_approval",
    ]

    # 本次运行已经批准过烧录：设备修复回路的重烧录不再重复询问。
    if state.get("approval_status") == "approved":
        return {"trace": trace}

    port = runtime.context.serial_port
    if port is None:
        # 缺少串口是运行配置问题，不是用户决策，直接以脱敏错误终止。
        return {
            "error": WorkflowError.model_validate(
                {
                    "stage": "flash",
                    "category": "serial",
                    "message": "未配置烧录串口",
                    "retryable": False,
                    "user_suggestion": "请通过 --port 指定开发板串口",
                }
            ),
            "trace": trace,
        }

    request = ApprovalRequest(
        project_name=runtime.context.project_path.name,
        port=port,
        target_chip=(
            runtime.context.target_chip
            or state["requirement"].target
        ),
        summary=_APPROVAL_SUMMARY,
        step_description="flash_project",
        attempts=state.get("flash_attempts", 0),
    )

    # interrupt() 在这里暂停整个 Graph；恢复值由 Runner 的
    # Command(resume={"approved": ...}) 提供，JSON 载荷可安全进 checkpoint。
    decision = interrupt(request.model_dump(mode="json"))
    approved = (
        isinstance(decision, dict)
        and bool(decision.get("approved"))
    )

    if approved:
        return {
            "approval_status": "approved",
            "approval_request": request,
            "trace": trace,
        }

    return {
        "approval_status": "rejected",
        "approval_request": request,
        "error": WorkflowError.model_validate(
            {
                "stage": "flash",
                "category": "approval_rejected",
                "message": _APPROVAL_REJECTED_MESSAGE,
                "retryable": False,
                "user_suggestion": _APPROVAL_REJECTED_SUGGESTION,
            }
        ),
        "trace": trace,
    }


def flash_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    port = runtime.context.serial_port
    if port is None:
        raise EspIdfError(
            category="serial",
            message="未配置烧录串口",
            retryable=False,
        )

    # 如果设备适配器提供协调式 flash→monitor 能力，由适配器保证两个
    # 有界进程严格串行，烧录完成后再进入短窗口 monitor。
    combined = getattr(runtime.context.flasher, "flash_and_monitor", None)
    monitor_evidence: MonitorEvidence | None = None
    if callable(combined):
        raw_evidence, raw_monitor = combined(
            runtime.context.project_path,
            port,
            runtime.context.monitor_timeout_seconds,
        )
        evidence = (
            raw_evidence
            if isinstance(raw_evidence, FlashEvidence)
            else FlashEvidence.model_validate(raw_evidence)
        )
        monitor_evidence = (
            raw_monitor
            if isinstance(raw_monitor, MonitorEvidence)
            else MonitorEvidence.model_validate(raw_monitor)
        )
    else:
        # 兼容旧 Adapter/Fake：没有协调能力时仍按原有 flash→monitor 路由。
        evidence = runtime.context.flasher.flash(
            runtime.context.project_path,
            port,
        )

    update: dict[str, object] = {
        "flash_evidence": evidence,
        "flash_attempts": state.get("flash_attempts", 0) + 1,
        "status": "flashing",
        "flash_monitor_combined": monitor_evidence is not None,
        "trace": [
            *state.get("trace", []),
            "flash_project",
        ],
    }
    if monitor_evidence is not None:
        update["monitor_evidence"] = monitor_evidence
    return update


def build_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    espidf = runtime.context.espidf
    project_path = runtime.context.project_path
    # 构建成功与否只能由 EspIdfPort 返回的真实证据决定，LLM 无权伪造。
    evidence = espidf.build(project_path)

    # get 在首次构建没有 attempts 键时使用 0；只有实际构建才增加次数。
    next_attempt = state.get("attempts", 0) + 1

    return {
        "build_evidence": evidence,
        "attempts": next_attempt,
        "status": "building",
        "trace": [
            *state.get("trace", []),
            "build_project",
        ],
    }


def request_clarification(
    state: WorkflowState,
) -> dict[str, object]:
    if not state.get("interactive_workflow"):
        return {
            "status": "needs_clarification",
            "trace": [*state.get("trace", []), "request_clarification"],
        }
    missing = state["requirement"].blocking_missing_fields
    interaction = WorkflowInteraction(
        kind="clarification",
        title="补充实现所需信息",
        summary="这些信息会直接影响实现；回复后我会沿用本次上下文继续规划。",
        questions=[f"请补充：{field}" for field in missing],
        options=[],
    )
    raw = interrupt(interaction.model_dump(mode="json"))
    decision = WorkflowDecision.model_validate(raw if isinstance(raw, dict) else {})
    feedback = " ".join(
        item for item in (decision.selected_option, decision.feedback.strip()) if item
    ).strip()
    trace = [*state.get("trace", []), "request_clarification"]
    if feedback:
        return {
            "task_text": state["task_text"] + "\n用户补充：" + feedback,
            "interaction": interaction,
            "interaction_action": "replan",
            "status": "needs_clarification",
            "trace": trace,
        }
    return {
        "interaction": interaction,
        "interaction_action": "failed",
        "error": WorkflowError.model_validate({
            "stage": "requirement_analysis",
            "category": "approval_rejected",
            "message": "用户取消了需求澄清",
            "retryable": False,
            "user_suggestion": "补充缺失信息后重新发起任务",
        }),
        "trace": trace,
    }


def completed(
    state: WorkflowState,
) -> dict[str, object]:
    # 终态节点只标记结果和轨迹，不再执行工具。
    return {
        "status": "completed",
        "trace": [
            *state.get("trace", []),
            "completed",
        ],
    }


def failed(
    state: WorkflowState,
) -> dict[str, object]:
    # 失败终态保留此前 State 中的最后证据，便于排查和向用户解释。
    return {
        "status": "failed",
        "trace": [
            *state.get("trace", []),
            "failed",
        ],
    }


def repair_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    interaction = WorkflowInteraction(
        kind="repair_review",
        title="确认生成并写入修复代码",
        summary=(
            "构建或设备验证发现了原计划之外的问题。批准后我会依据当前源码和"
            "受控诊断生成修复并重新构建；拒绝则保留现有文件并结束任务。"
        ),
        options=["批准修复", "停止并保留当前文件"],
        operation={"action": "repair_code", "origin": state.get("repair_origin") or "build"},
        allow_feedback=False,
    )
    if runtime.context.interactive_workflow:
        raw = interrupt(interaction.model_dump(mode="json"))
        decision = WorkflowDecision.model_validate(raw if isinstance(raw, dict) else {})
        if not decision.approved:
            return {
                "interaction": interaction,
                "error": WorkflowError.model_validate({
                    "stage": "repair", "category": "approval_rejected",
                    "message": "修复代码未获批准，未执行新的源码写入",
                    "retryable": False, "user_suggestion": "可检查构建诊断后重新规划",
                }),
                "trace": [*state.get("trace", []), "repair_project"],
            }
    project_path = runtime.context.project_path
    workspace = runtime.context.workspace
    repair_planner = runtime.context.repair_planner

    # 先由 Workspace 受控读取文件，RepairPlanner 本身没有任意磁盘访问权限。
    files = workspace.read_project_files(project_path)

    # 设备回路修复携带日志诊断；纯构建修复传 None。
    device_diagnosis = state.get("device_diagnosis")

    # 修复模型同时看到需求、原计划、失败证据和源码，返回经过验证的 RepairPlan。
    repair = repair_planner.create_repair(
        state["requirement"],
        state["plan"],
        state["build_evidence"],
        files,
        device_diagnosis=device_diagnosis,
    )

    # 只有 Workspace 能产生写文件副作用，并返回实际修改的相对路径。
    changed_files = workspace.apply_repair(
        project_path,
        repair,
    )

    # Once files change, the old snapshot is invalid. Refresh immediately so a
    # later repair/monitor step never reasons from the pre-repair code.
    refreshed = analyze_current_project(
        project_path=project_path,
        target_chip=runtime.context.target_chip,
        workspace=workspace,
        analyzer=runtime.context.project_analyzer,
        persistence=runtime.context.persistence,
        project_key=runtime.context.project_key,
        force=True,
    )

    # 记录本次修复的触发来源：路由据此决定重建成功后的去向。
    repair_origin = (
        "monitor" if device_diagnosis is not None else "build"
    )

    # 此处不返回 attempts 和 build_evidence，因此旧值会保留；下一次构建才覆盖证据。
    return {
        "repair_plan": repair,
        "changed_files": [
            *state.get("changed_files", []),
            *changed_files,
        ],
        "project_analysis": refreshed,
        "repair_origin": repair_origin,
        "status": "repaired",
        "trace": [
            *state.get("trace", []),
            "repair_project",
        ],
    }


# 设备修复回路的最大轮数；耗尽后终止，防止硬件回路无限运行。
_MAX_DEVICE_CYCLES = 3

_DEVICE_BUDGET_MESSAGE = "设备修复循环达到上限"

_DEVICE_BUDGET_SUGGESTION = "请人工检查硬件连接与固件逻辑"

_NO_REPAIR_MESSAGE = "设备运行日志异常但未发现可修复项"

_NO_REPAIR_SUGGESTION = "请人工检查设备运行日志"


def monitor_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    port = runtime.context.serial_port
    if port is None:
        raise EspIdfError(
            category="serial",
            message="未配置监控串口",
            retryable=False,
        )

    # 采集窗口由 Context 配置；超时是正常结束方式而不是失败。
    evidence = runtime.context.monitor.monitor(
        runtime.context.project_path,
        port,
        runtime.context.monitor_timeout_seconds,
    )

    return {
        "monitor_evidence": evidence,
        "status": "monitoring",
        "trace": [
            *state.get("trace", []),
            "monitor_project",
        ],
    }


def analyze_device_logs(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    # 只有 LogAnalystPort 能把日志变成诊断；LLM 无权直接宣称设备健康。
    diagnosis = runtime.context.log_analyst.analyze(
        state["requirement"],
        state["monitor_evidence"],
    )
    cycles = state.get("device_cycles", 0) + 1

    update: dict[str, object] = {
        "device_diagnosis": diagnosis,
        "device_cycles": cycles,
        "status": "diagnosed",
        "trace": [
            *state.get("trace", []),
            "analyze_device_logs",
        ],
    }

    # 需要修复但预算耗尽：以固定脱敏错误终止。
    if diagnosis.repair_needed and cycles > _MAX_DEVICE_CYCLES:
        update["error"] = WorkflowError.model_validate(
            {
                "stage": "monitor",
                "category": "unknown",
                "message": _DEVICE_BUDGET_MESSAGE,
                "retryable": False,
                "user_suggestion": _DEVICE_BUDGET_SUGGESTION,
            }
        )

    # 不健康但无修复建议：同样终止，避免虚假完成。
    if not diagnosis.healthy and not diagnosis.repair_needed:
        update["error"] = WorkflowError.model_validate(
            {
                "stage": "monitor",
                "category": "unknown",
                "message": _NO_REPAIR_MESSAGE,
                "retryable": False,
                "user_suggestion": _NO_REPAIR_SUGGESTION,
            }
        )

    return update
