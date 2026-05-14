from __future__ import annotations

from pathlib import Path
import re
from typing import Generator, Iterator

from luxar.core.config_manager import AgentConfig
from luxar.core.document_engineering import DocumentEngineeringAnalyzer
from luxar.core.project_manager import ProjectManager
from luxar.core.task_router import TaskRouter
from luxar.models.schemas import EngineeringContext, ReviewReport, TaskArtifacts, TaskRunResult
from luxar.tools.build_project import run_build_project
from luxar.tools.forge_project import run_forge_project, run_forge_project_stream
from luxar.tools.fix_code import run_fix_code
from luxar.tools.generate_driver_loop import run_generate_driver_loop
from luxar.tools.review_code import run_review_project
from luxar.tools.run_workflow import run_debug_workflow


def _stream_event(event_type: str, **payload) -> dict:
    return {"type": event_type, **payload}


def _build_task_result(
    *,
    success: bool,
    mode: str,
    intent: str,
    execution_plan,
    message: str = "",
    engineering: EngineeringContext | None = None,
    project: dict | None = None,
    workflow: dict | None = None,
    driver_pipeline: dict | None = None,
    build_result: dict | None = None,
    report: dict | None = None,
    fixed_files: list[str] | None = None,
    driver_request: dict | None = None,
) -> dict:
    payload = TaskRunResult(
        success=success,
        mode=mode,
        intent=intent,
        execution_plan=execution_plan.model_dump(mode="json") if hasattr(execution_plan, "model_dump") else dict(execution_plan),
        message=message,
        engineering_context=engineering.model_dump(mode="json") if engineering else {},
        artifacts=TaskArtifacts(
            project=project or {},
            workflow=workflow or {},
            driver_pipeline=driver_pipeline or {},
            build_result=build_result or {},
            report=report or {},
            fixed_files=fixed_files or [],
            driver_request=driver_request or {},
        ),
    ).model_dump(mode="json")
    artifacts = payload.get("artifacts", {})
    # Compatibility layer for current UI/tests while we migrate callers to `artifacts.*`.
    payload["project"] = artifacts.get("project", {})
    payload["workflow"] = artifacts.get("workflow", {})
    payload["driver_pipeline"] = artifacts.get("driver_pipeline", {})
    payload["build_result"] = artifacts.get("build_result", {})
    payload["report"] = artifacts.get("report", {})
    payload["fixed_files"] = artifacts.get("fixed_files", [])
    payload["driver_request"] = artifacts.get("driver_request", {})
    return payload


def _prepare_task_execution(
    *,
    task: str,
    project_name: str,
    docs: list[str],
    dry_run: bool,
    plan_only: bool,
    driver_library_root: str,
) -> tuple:
    router = TaskRouter()
    execution_plan = router.route(
        task=task,
        project=project_name,
        docs=docs,
        dry_run=dry_run,
        plan_only=plan_only,
    )
    engineering = _build_engineering_context(
        driver_library_root=driver_library_root,
        docs=docs,
        query=task,
    ) if docs else None
    return execution_plan, engineering


def _consume_stream_result(stream: Generator[dict, None, dict]) -> dict:
    while True:
        try:
            next(stream)
        except StopIteration as stop:
            return stop.value


def _stream_project_status(
    *,
    workspace_root: str,
    project_name: str,
    execution_plan,
    workflow_name: str,
) -> Generator[dict, None, dict]:
    yield _stream_event("workflow_step_started", workflow=workflow_name, step="status", message="读取项目状态")
    project = ProjectManager(workspace_root).load_project(project_name)
    result = _build_task_result(
        success=True,
        mode="status",
        intent="project_status",
        execution_plan=execution_plan,
        message=f"当前项目是 {project.name}，MCU 是 {project.mcu}，平台是 {project.platform}，运行时是 {project.runtime}。",
        project=project.model_dump(mode="json"),
    )
    yield _stream_event(
        "workflow_step_finished",
        workflow=workflow_name,
        step="status",
        status="completed",
        message=result["message"],
        payload={"project": result["project"]},
    )
    return result


def _stream_review_or_fix(
    *,
    config: AgentConfig,
    workspace_root: str,
    task: str,
    project_name: str,
    execution_plan,
) -> Generator[dict, None, dict]:
    project = ProjectManager(workspace_root).load_project(project_name)
    yield _stream_event("workflow_step_started", step="review", message="审查当前项目代码")
    report = run_review_project(project.path)
    review_payload = report.get("report", {})
    initial_passed = bool(review_payload.get("passed", False))
    yield _stream_event(
        "workflow_step_finished",
        step="review",
        status="completed" if initial_passed else "failed",
        message="代码审查完成。",
        payload=report,
    )

    fixed_files: list[str] = []
    auto_fix_rule_ids = _auto_fix_rule_ids(config)
    if _should_auto_apply_review_fixes(task=task, report=review_payload, auto_fix_rule_ids=auto_fix_rule_ids):
        yield _stream_event("workflow_step_started", step="fix", message="自动修复 App/ 审查问题")
        fixed_files = _auto_fix_review_files(
            config=config,
            project_path=project.path,
            report=review_payload,
            auto_fix_rule_ids=auto_fix_rule_ids,
        )
        yield _stream_event(
            "workflow_step_finished",
            step="fix",
            status="completed" if fixed_files else "skipped",
            message=f"自动修复完成，修改了 {len(fixed_files)} 个文件。" if fixed_files else "没有可自动修复的文件。",
            payload={"fixed_files": fixed_files},
        )
        if fixed_files:
            yield _stream_event("workflow_step_started", step="rereview", message="重新审查修复后的代码")
            report = run_review_project(project.path)
            review_payload = report.get("report", {})
            yield _stream_event(
                "workflow_step_finished",
                step="rereview",
                status="completed" if review_payload.get("passed", False) else "failed",
                message="重新审查完成。",
                payload=report,
            )

    return _build_task_result(
        success=True,
        mode="fix" if fixed_files else "review",
        intent="review_or_fix",
        execution_plan=execution_plan,
        message=_build_review_message(review_payload, fixed_files=fixed_files),
        report=report,
        fixed_files=fixed_files,
    )


def _stream_generate_driver(
    *,
    config: AgentConfig,
    project_root: str,
    task: str,
    engineering: EngineeringContext | None,
    execution_plan,
    dry_run: bool,
    plan_only: bool,
) -> Generator[dict, None, dict]:
    chip, interface = _infer_driver_request(task=task, engineering=engineering)
    if not chip or not interface:
        return _build_task_result(
            success=False,
            mode="plan",
            intent="generate_driver",
            execution_plan=execution_plan,
            engineering=engineering,
            message="Need at least a chip/device name and interface before generating a driver.",
        )
    if plan_only or dry_run:
        return _build_task_result(
            success=True,
            mode="plan",
            intent="generate_driver",
            execution_plan=execution_plan,
            engineering=engineering,
            message="驱动生成已进入计划模式。",
            driver_request={"chip": chip, "interface": interface},
        )
    yield _stream_event("workflow_step_started", step="generate_driver", message=f"生成 {chip} 的 {interface} 驱动")
    pipeline = run_generate_driver_loop(
        config=config,
        project_root=project_root,
        chip=chip,
        interface=interface,
        doc_summary=engineering.document_summary if engineering else task,
    )
    result = _build_task_result(
        success=bool(pipeline.success),
        mode="execute",
        intent="generate_driver",
        execution_plan=execution_plan,
        engineering=engineering,
        message="驱动工作流完成。" if pipeline.success else (pipeline.error or "驱动工作流失败。"),
        driver_pipeline=pipeline.model_dump(mode="json"),
        driver_request={"chip": chip, "interface": interface},
    )
    yield _stream_event(
        "workflow_step_finished",
        step="generate_driver",
        status="completed" if pipeline.success else "failed",
        message=result["message"],
        payload=result["driver_pipeline"],
    )
    return result


def _stream_debug_project(
    *,
    config: AgentConfig,
    project_root: str,
    workspace_root: str,
    task: str,
    project_name: str,
    execution_plan,
    engineering: EngineeringContext | None,
    dry_run: bool,
    plan_only: bool,
    no_flash: bool,
    no_monitor: bool,
) -> Generator[dict, None, dict]:
    project = ProjectManager(workspace_root).load_project(project_name)
    if plan_only or dry_run:
        return _build_task_result(
            success=True,
            mode="plan",
            intent="debug_project",
            execution_plan=execution_plan,
            engineering=engineering,
            message="调试工作流已进入计划模式。",
        )

    if _should_run_build_only(task):
        yield _stream_event("workflow_step_started", step="build", message="构建工程")
        build_result = run_build_project(
            project_path=project.path,
            config=config,
            project_root=project_root,
            clean=False,
            skip_review=False,
        )
        result = _build_task_result(
            success=bool(build_result.success),
            mode="execute",
            intent="debug_project",
            execution_plan=execution_plan,
            message=_build_build_message(build_result.model_dump(mode="json")),
            build_result=build_result.model_dump(mode="json"),
        )
        yield _stream_event(
            "workflow_step_finished",
            step="build",
            status="completed" if build_result.success else "failed",
            message=result["message"],
            payload=result["build_result"],
        )
        return result

    yield _stream_event("workflow_step_started", step="debug", message="执行 build/flash/monitor 调试流程")
    workflow_result = run_debug_workflow(
        config=config,
        project_root=project_root,
        project_path=project.path,
        probe=None if no_flash else None,
        port="" if no_monitor else "",
        clean=False,
    )
    workflow_payload = workflow_result.model_dump(mode="json")
    yield _stream_event(
        "workflow_step_finished",
        step="debug",
        status="completed" if workflow_result.success else "failed",
        message=workflow_result.summary,
        payload=workflow_payload,
    )
    for step in workflow_result.steps:
        yield _stream_event(
            "workflow_step_finished",
            step=step.name,
            status=step.status,
            message=step.message,
            payload=step.payload,
        )
    return _build_task_result(
        success=bool(workflow_result.success),
        mode="execute",
        intent="debug_project",
        execution_plan=execution_plan,
        message=workflow_result.summary,
        workflow=workflow_payload,
    )


def _stream_forge_project(
    *,
    config: AgentConfig,
    project_root: str,
    workspace_root: str,
    driver_library_root: str,
    task: str,
    project_name: str,
    docs: list[str],
    execution_plan,
    engineering: EngineeringContext | None,
    dry_run: bool,
    plan_only: bool,
    no_build: bool,
    no_flash: bool,
    no_monitor: bool,
    stream_mode: bool,
) -> Generator[dict, None, dict]:
    project = ProjectManager(workspace_root).load_project(project_name)
    mode = "execute" if not (plan_only or dry_run) else "plan"
    if not stream_mode:
        workflow = run_forge_project(
            config=config,
            project_root=project_root,
            project=project,
            requirement=task,
            driver_library_root=driver_library_root,
            plan_only=plan_only or dry_run,
            build=not (no_build or dry_run or plan_only),
            no_flash=no_flash,
            no_monitor=no_monitor,
            docs=docs,
            doc_query=task,
        )
        return _build_task_result(
            success=workflow.success,
            mode=mode,
            intent="forge_project",
            execution_plan=execution_plan,
            engineering=engineering,
            message=workflow.summary,
            workflow=workflow.model_dump(mode="json"),
        )

    workflow_payload: dict | None = None
    last_message = ""
    for event in run_forge_project_stream(
        config=config,
        project_root=project_root,
        project=project,
        requirement=task,
        driver_library_root=driver_library_root,
        plan_only=plan_only or dry_run,
        build=not (no_build or dry_run or plan_only),
        no_flash=no_flash,
        no_monitor=no_monitor,
        docs=docs,
        doc_query=task,
    ):
        if event["type"] in {"workflow_finished", "workflow_failed"}:
            workflow_payload = event.get("payload", {}).get("result", {})
            last_message = event.get("message", "")
            continue
        yield _stream_event(
            event["type"],
            step=event.get("step", ""),
            message=event.get("message", ""),
            status=event.get("status", ""),
            payload=event.get("payload", {}),
        )
    if workflow_payload is None:
        return _build_task_result(
            success=False,
            mode=mode,
            intent="forge_project",
            execution_plan=execution_plan,
            engineering=engineering,
            message="Forge workflow produced no events.",
        )
    return _build_task_result(
        success=workflow_payload.get("success", False),
        mode=mode,
        intent="forge_project",
        execution_plan=execution_plan,
        engineering=engineering,
        message=last_message,
        workflow=workflow_payload,
    )


def _stream_task_core(
    *,
    config: AgentConfig,
    project_root: str,
    workspace_root: str,
    driver_library_root: str,
    task: str,
    project_name: str,
    docs: list[str],
    execution_plan,
    engineering: EngineeringContext | None,
    dry_run: bool,
    plan_only: bool,
    no_build: bool,
    no_flash: bool,
    no_monitor: bool,
    stream_mode: bool,
) -> Generator[dict, None, dict]:
    if execution_plan.missing_info_questions:
        return _build_task_result(
            success=False,
            mode="plan",
            intent=execution_plan.intent.intent_type,
            execution_plan=execution_plan,
            engineering=engineering,
            message=execution_plan.missing_info_questions[0],
        )

    intent = execution_plan.intent.intent_type
    if intent == "explain":
        return _build_task_result(
            success=True,
            mode="explain",
            intent=intent,
            execution_plan=execution_plan,
            engineering=engineering,
            message=_build_explain_message(task=task, engineering=engineering),
        )

    if intent == "project_status":
        return (yield from _stream_project_status(
            workspace_root=workspace_root,
            project_name=project_name,
            execution_plan=execution_plan,
            workflow_name=execution_plan.intent.recommended_workflow or execution_plan.intent.intent_type,
        ))

    if intent == "review_or_fix":
        return (yield from _stream_review_or_fix(
            config=config,
            workspace_root=workspace_root,
            task=task,
            project_name=project_name,
            execution_plan=execution_plan,
        ))

    if intent == "debug_project":
        return (yield from _stream_debug_project(
            config=config,
            project_root=project_root,
            workspace_root=workspace_root,
            task=task,
            project_name=project_name,
            execution_plan=execution_plan,
            engineering=engineering,
            dry_run=dry_run,
            plan_only=plan_only,
            no_flash=no_flash,
            no_monitor=no_monitor,
        ))

    if intent == "generate_driver":
        return (yield from _stream_generate_driver(
            config=config,
            project_root=project_root,
            task=task,
            engineering=engineering,
            execution_plan=execution_plan,
            dry_run=dry_run,
            plan_only=plan_only,
        ))

    if intent == "forge_project":
        return (yield from _stream_forge_project(
            config=config,
            project_root=project_root,
            workspace_root=workspace_root,
            driver_library_root=driver_library_root,
            task=task,
            project_name=project_name,
            docs=docs,
            execution_plan=execution_plan,
            engineering=engineering,
            dry_run=dry_run,
            plan_only=plan_only,
            no_build=no_build,
            no_flash=no_flash,
            no_monitor=no_monitor,
            stream_mode=stream_mode,
        ))

    return _build_task_result(
        success=True,
        mode="plan",
        intent=intent,
        execution_plan=execution_plan,
        engineering=engineering,
        message="任务已进入计划模式。",
    )


def run_task_stream(
    *,
    config: AgentConfig,
    project_root: str,
    workspace_root: str,
    driver_library_root: str,
    task: str,
    project_name: str = "",
    docs: list[str] | None = None,
    dry_run: bool = False,
    plan_only: bool = False,
    no_build: bool = False,
    no_flash: bool = False,
    no_monitor: bool = False,
) -> Iterator[dict]:
    docs = docs or []
    execution_plan, engineering = _prepare_task_execution(
        task=task,
        project_name=project_name,
        docs=docs,
        dry_run=dry_run,
        plan_only=plan_only,
        driver_library_root=driver_library_root,
    )

    workflow_name = execution_plan.intent.recommended_workflow or execution_plan.intent.intent_type
    yield _stream_event(
        "workflow_started",
        workflow=workflow_name,
        message=f"开始执行任务：{task.strip()}",
        payload={
            "execution_plan": execution_plan.model_dump(mode="json"),
            "engineering_context": engineering.model_dump(mode="json") if engineering else {},
        },
    )

    stream = _stream_task_core(
        config=config,
        project_root=project_root,
        workspace_root=workspace_root,
        driver_library_root=driver_library_root,
        task=task,
        project_name=project_name,
        docs=docs,
        execution_plan=execution_plan,
        engineering=engineering,
        dry_run=dry_run,
        plan_only=plan_only,
        no_build=no_build,
        no_flash=no_flash,
        no_monitor=no_monitor,
        stream_mode=True,
    )
    while True:
        try:
            event = next(stream)
        except StopIteration as stop:
            result = stop.value
            break
        if "workflow" not in event:
            event["workflow"] = workflow_name
        yield event
    final_type = "workflow_finished" if result["success"] else "workflow_failed"
    yield _stream_event(final_type, workflow=workflow_name, message=result["message"], payload={"result": result})
    return


def run_task(
    *,
    config: AgentConfig,
    project_root: str,
    workspace_root: str,
    driver_library_root: str,
    task: str,
    project_name: str = "",
    docs: list[str] | None = None,
    dry_run: bool = False,
    plan_only: bool = False,
    no_build: bool = False,
    no_flash: bool = False,
    no_monitor: bool = False,
) -> dict:
    docs = docs or []
    execution_plan, engineering = _prepare_task_execution(
        task=task,
        project_name=project_name,
        docs=docs,
        dry_run=dry_run,
        plan_only=plan_only,
        driver_library_root=driver_library_root,
    )

    return _consume_stream_result(_stream_task_core(
        config=config,
        project_root=project_root,
        workspace_root=workspace_root,
        driver_library_root=driver_library_root,
        task=task,
        project_name=project_name,
        docs=docs,
        execution_plan=execution_plan,
        engineering=engineering,
        dry_run=dry_run,
        plan_only=plan_only,
        no_build=no_build,
        no_flash=no_flash,
        no_monitor=no_monitor,
        stream_mode=False,
    ))


def _build_engineering_context(*, driver_library_root: str, docs: list[str], query: str):
    analyzer = DocumentEngineeringAnalyzer(Path(driver_library_root).resolve() / "knowledge_base")
    return analyzer.analyze(docs=docs, query=query)


def _infer_driver_request(*, task: str, engineering: EngineeringContext | None) -> tuple[str, str]:
    interface = ""
    if engineering and engineering.bus_requirements:
        interface = engineering.bus_requirements[0].interface.upper()
    else:
        lowered = task.lower()
        for candidate in ("spi", "i2c", "uart"):
            if candidate in lowered:
                interface = candidate.upper()
                break

    chip = ""
    if engineering:
        for hint in engineering.register_hints:
            if any(char.isdigit() for char in hint):
                chip = hint
                break
    if not chip:
        match = re.search(r"\b([A-Za-z]{2,}\d[A-Za-z0-9_-]*)\b", task)
        if match:
            chip = match.group(1)
    return chip, interface


def _build_explain_message(*, task: str, engineering: EngineeringContext | None) -> str:
    summary = (engineering.document_summary if engineering else "").strip()
    if summary:
        return summary

    lowered = task.strip().lower()
    if any(token in lowered for token in ["你有什么功能", "你能做什么", "功能", "capability", "what can you do", "help"]):
        return "我可以帮你看文档、提取引脚和协议要求、生成或复用驱动、规划 STM32 工程、审查代码、修复一部分编译问题，以及执行 build、flash、monitor、debug 这类流程。你可以直接告诉我目标，比如“审查当前项目代码”或“基于这个 PDF 生成工程”。"
    if lowered in {"hi", "hello", "hey", "你好", "您好", "嗨", "在吗"}:
        return "你好，我在。你可以直接告诉我你想做什么，比如看文档、分析引脚、生成工程、审查代码，或者修复编译问题。"

    if not lowered:
        return "请直接告诉我你的目标，我可以帮你解释文档、规划项目、生成驱动，或者调试当前工程。"

    return f"我理解到你的请求是：{task.strip()}。如果你愿意，我可以继续帮你分析文档、规划工程步骤，或者直接执行对应工作流。"


def _should_run_build_only(task: str) -> bool:
    lowered = task.lower()
    build_tokens = ("build", "compile", "rebuild", "编译", "构建", "重建")
    debug_tokens = ("flash", "monitor", "debug", "probe", "port", "串口", "烧录", "调试", "st-link", "stlink")
    return any(token in lowered for token in build_tokens) and not any(token in lowered for token in debug_tokens)


def _build_build_message(result: dict) -> str:
    if result.get("success"):
        warnings = len(result.get("warnings", []) or [])
        return f"构建已经完成并通过。当前共有 {warnings} 条警告。"
    errors = result.get("errors", []) or []
    first_error = errors[0] if errors else result.get("stderr", "构建失败。")
    return f"构建未通过。首个阻塞问题是：{first_error}"


def _auto_fix_rule_ids(config: AgentConfig) -> set[str]:
    if not config.review.auto_fix_enabled:
        return set()
    return {rule_id.strip().upper() for rule_id in config.review.auto_fix_rule_ids if rule_id.strip()}


def _should_auto_apply_review_fixes(*, task: str, report: dict, auto_fix_rule_ids: set[str]) -> bool:
    lowered = task.lower()
    fix_requested = any(token in lowered for token in ("fix", "修复", "replace", "printf", "doxygen", "注释"))
    if not fix_requested:
        return False
    issues = report.get("issues", []) or []
    blocking = [issue for issue in issues if issue.get("severity") in {"critical", "error"}]
    if not blocking:
        return False
    return all(_is_auto_fixable_app_issue(issue, auto_fix_rule_ids) for issue in blocking)


def _auto_fix_review_files(
    *,
    config: AgentConfig,
    project_path: str,
    report: dict,
    auto_fix_rule_ids: set[str],
) -> list[str]:
    target_files = sorted({
        issue.get("file", "")
        for issue in report.get("issues", []) or []
        if _is_auto_fixable_app_issue(issue, auto_fix_rule_ids)
    })
    fixed_files: list[str] = []
    for file_path in target_files:
        if not file_path:
            continue
        scoped_issues = [
            issue
            for issue in report.get("issues", []) or []
            if issue.get("file", "") == file_path and _is_auto_fixable_app_issue(issue, auto_fix_rule_ids)
        ]
        scoped_report = ReviewReport(
            passed=False,
            total_issues=len(scoped_issues),
            critical_count=sum(1 for issue in scoped_issues if issue.get("severity") == "critical"),
            error_count=sum(1 for issue in scoped_issues if issue.get("severity") == "error"),
            warning_count=sum(1 for issue in scoped_issues if issue.get("severity") == "warning"),
            issues=scoped_issues,
            raw_logs={},
        )
        result = run_fix_code(
            config=config,
            project_path=project_path,
            file_path=file_path,
            apply_changes=True,
            review_report=scoped_report,
        )
        if result.success and result.applied:
            fixed_files.append(str(Path(file_path).resolve()))
    return fixed_files


def _is_auto_fixable_app_issue(issue: dict, auto_fix_rule_ids: set[str]) -> bool:
    file_path = issue.get("file", "")
    parts = {part.lower() for part in Path(file_path).parts}
    rule_id = str(issue.get("rule_id", "")).upper()
    return "app" in parts and rule_id in auto_fix_rule_ids


def _build_review_message(report: dict, *, fixed_files: list[str] | None = None) -> str:
    fixed_files = fixed_files or []
    total = int(report.get("total_issues", 0) or 0)
    critical = int(report.get("critical_count", 0) or 0)
    errors = int(report.get("error_count", 0) or 0)
    warnings = int(report.get("warning_count", 0) or 0)
    if fixed_files and report.get("passed", False):
        return f"我已经自动修复了 {len(fixed_files)} 个文件，并重新审查通过。当前共有 {warnings} 个警告。"
    if fixed_files:
        return (
            f"我已经自动修复了 {len(fixed_files)} 个文件，并重新审查。"
            f"当前仍有 {total} 个问题，其中严重 {critical} 个、错误 {errors} 个、警告 {warnings} 个。"
        )
    if report.get("passed", False):
        return f"我已经审查了当前项目代码，结果通过，没有阻塞性问题。当前共有 {warnings} 个警告。"
    return f"我已经审查了当前项目代码，结果未通过。当前共有 {total} 个问题，其中严重 {critical} 个、错误 {errors} 个、警告 {warnings} 个。"
