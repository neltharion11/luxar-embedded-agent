from __future__ import annotations

from typing import Any, Callable, Generator

from luxar.core.config_manager import AgentConfig
from luxar.models.schemas import EngineeringContext


def stream_project_status(
    *,
    workspace_root: str,
    project_name: str,
    execution_plan,
    workflow_name: str,
    project_manager_cls,
    stream_event: Callable[..., dict],
    build_task_result: Callable[..., dict],
) -> Generator[dict, None, dict]:
    yield stream_event("workflow_step_started", workflow=workflow_name, step="status", message="读取项目状态")
    project = project_manager_cls(workspace_root).load_project(project_name)
    result = build_task_result(
        success=True,
        mode="status",
        intent=execution_plan.intent.intent_type,
        execution_plan=execution_plan,
        message=f"当前项目是 {project.name}，MCU 是 {project.mcu}，平台是 {project.platform}，运行时是 {project.runtime}。",
        project=project.model_dump(mode="json"),
    )
    yield stream_event(
        "workflow_step_finished",
        workflow=workflow_name,
        step="status",
        status="completed",
        message=result["message"],
        payload={"project": result["project"]},
    )
    return result


def stream_review_or_fix(
    *,
    config: AgentConfig,
    workspace_root: str,
    task: str,
    project_name: str,
    execution_plan,
    project_manager_cls,
    runtime_adapters_module,
    stream_event: Callable[..., dict],
    build_task_result: Callable[..., dict],
    auto_fix_rule_ids: Callable[[AgentConfig], set[str]],
    should_auto_apply_review_fixes: Callable[..., bool],
    auto_fix_review_files: Callable[..., list[str]],
    build_review_message: Callable[..., str],
) -> Generator[dict, None, dict]:
    project = project_manager_cls(workspace_root).load_project(project_name)
    yield stream_event("workflow_step_started", step="review", message="审查当前项目代码")
    report = runtime_adapters_module.run_review(project.path)
    review_payload = report.get("report", {})
    initial_passed = bool(review_payload.get("passed", False))
    yield stream_event(
        "workflow_step_finished",
        step="review",
        status="completed" if initial_passed else "failed",
        message="代码审查完成。",
        payload=report,
    )

    fixed_files: list[str] = []
    fix_rule_ids = auto_fix_rule_ids(config)
    if should_auto_apply_review_fixes(task=task, report=review_payload, auto_fix_rule_ids=fix_rule_ids):
        yield stream_event("workflow_step_started", step="fix", message="自动修复 App/ 审查问题")
        fixed_files = auto_fix_review_files(
            config=config,
            project_path=project.path,
            report=review_payload,
            auto_fix_rule_ids=fix_rule_ids,
        )
        yield stream_event(
            "workflow_step_finished",
            step="fix",
            status="completed" if fixed_files else "skipped",
            message=f"自动修复完成，修改了 {len(fixed_files)} 个文件。" if fixed_files else "没有可自动修复的文件。",
            payload={"fixed_files": fixed_files},
        )
        if fixed_files:
            yield stream_event("workflow_step_started", step="rereview", message="重新审查修复后的代码")
            report = runtime_adapters_module.run_review(project.path)
            review_payload = report.get("report", {})
            yield stream_event(
                "workflow_step_finished",
                step="rereview",
                status="completed" if review_payload.get("passed", False) else "failed",
                message="重新审查完成。",
                payload=report,
            )

    return build_task_result(
        success=True,
        mode="fix" if fixed_files else "review",
        intent=execution_plan.intent.intent_type,
        execution_plan=execution_plan,
        message=build_review_message(review_payload, fixed_files=fixed_files),
        report=report,
        fixed_files=fixed_files,
    )


def stream_generate_driver(
    *,
    config: AgentConfig,
    project_root: str,
    task: str,
    engineering: EngineeringContext | None,
    execution_plan,
    dry_run: bool,
    plan_only: bool,
    runtime_adapters_module,
    stream_event: Callable[..., dict],
    build_task_result: Callable[..., dict],
    infer_driver_request: Callable[..., tuple[str, str]],
) -> Generator[dict, None, dict]:
    chip, interface = infer_driver_request(task=task, engineering=engineering)
    if not chip or not interface:
        return build_task_result(
            success=False,
            mode="plan",
            intent=execution_plan.intent.intent_type,
            execution_plan=execution_plan,
            engineering=engineering,
            message="Need at least a chip/device name and interface before generating a driver.",
        )
    if plan_only or dry_run:
        return build_task_result(
            success=True,
            mode="plan",
            intent=execution_plan.intent.intent_type,
            execution_plan=execution_plan,
            engineering=engineering,
            message="驱动生成已进入计划模式。",
            driver_request={"chip": chip, "interface": interface},
        )
    yield stream_event("workflow_step_started", step="generate_driver", message=f"生成 {chip} 的 {interface} 驱动")
    pipeline = runtime_adapters_module.run_generate_driver(
        config=config,
        project_root=project_root,
        chip=chip,
        interface=interface,
        doc_summary=engineering.document_summary if engineering else task,
    )
    result = build_task_result(
        success=bool(pipeline.success),
        mode="execute",
        intent=execution_plan.intent.intent_type,
        execution_plan=execution_plan,
        engineering=engineering,
        message="驱动工作流完成。" if pipeline.success else (pipeline.error or "驱动工作流失败。"),
        driver_pipeline=pipeline.model_dump(mode="json"),
        driver_request={"chip": chip, "interface": interface},
    )
    yield stream_event(
        "workflow_step_finished",
        step="generate_driver",
        status="completed" if pipeline.success else "failed",
        message=result["message"],
        payload=result["driver_pipeline"],
    )
    return result


def stream_debug_project(
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
    project_manager_cls,
    runtime_adapters_module,
    stream_event: Callable[..., dict],
    build_task_result: Callable[..., dict],
    should_run_build_only: Callable[[str], bool],
    build_build_message: Callable[[dict], str],
) -> Generator[dict, None, dict]:
    project = project_manager_cls(workspace_root).load_project(project_name)
    if plan_only or dry_run:
        return build_task_result(
            success=True,
            mode="plan",
            intent=execution_plan.intent.intent_type,
            execution_plan=execution_plan,
            engineering=engineering,
            message="调试工作流已进入计划模式。",
        )

    if should_run_build_only(task):
        yield stream_event("workflow_step_started", step="build", message="构建工程")
        build_result = runtime_adapters_module.run_build(
            project_path=project.path,
            config=config,
            project_root=project_root,
            clean=False,
            skip_review=False,
        )
        result = build_task_result(
            success=bool(build_result.success),
            mode="execute",
            intent=execution_plan.intent.intent_type,
            execution_plan=execution_plan,
            message=build_build_message(build_result.model_dump(mode="json")),
            build_result=build_result.model_dump(mode="json"),
        )
        yield stream_event(
            "workflow_step_finished",
            step="build",
            status="completed" if build_result.success else "failed",
            message=result["message"],
            payload=result["build_result"],
        )
        return result

    yield stream_event("workflow_step_started", step="debug", message="执行 build/flash/monitor 调试流程")
    workflow_result = runtime_adapters_module.run_debug(
        config=config,
        project_root=project_root,
        project_path=project.path,
        probe=None if no_flash else None,
        port="" if no_monitor else "",
        clean=False,
    )
    workflow_payload = workflow_result.model_dump(mode="json")
    yield stream_event(
        "workflow_step_finished",
        step="debug",
        status="completed" if workflow_result.success else "failed",
        message=workflow_result.summary,
        payload=workflow_payload,
    )
    for step in workflow_result.steps:
        yield stream_event(
            "workflow_step_finished",
            step=step.name,
            status=step.status,
            message=step.message,
            payload=step.payload,
        )
    return build_task_result(
        success=bool(workflow_result.success),
        mode="execute",
        intent=execution_plan.intent.intent_type,
        execution_plan=execution_plan,
        message=workflow_result.summary,
        workflow=workflow_payload,
    )


def stream_forge_project(
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
    project_manager_cls,
    runtime_adapters_module,
    stream_event: Callable[..., dict],
    build_task_result: Callable[..., dict],
) -> Generator[dict, None, dict]:
    project = project_manager_cls(workspace_root).load_project(project_name)
    mode = "execute" if not (plan_only or dry_run) else "plan"
    if not stream_mode:
        workflow = runtime_adapters_module.run_forge(
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
        return build_task_result(
            success=workflow.success,
            mode=mode,
            intent=execution_plan.intent.intent_type,
            execution_plan=execution_plan,
            engineering=engineering,
            message=workflow.summary,
            workflow=workflow.model_dump(mode="json"),
        )

    workflow_payload: dict | None = None
    last_message = ""
    for event in runtime_adapters_module.run_forge_stream(
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
        yield stream_event(
            event["type"],
            step=event.get("step", ""),
            message=event.get("message", ""),
            status=event.get("status", ""),
            payload=event.get("payload", {}),
        )
    if workflow_payload is None:
        return build_task_result(
            success=False,
            mode=mode,
            intent=execution_plan.intent.intent_type,
            execution_plan=execution_plan,
            engineering=engineering,
            message="Forge workflow produced no events.",
        )
    return build_task_result(
        success=workflow_payload.get("success", False),
        mode=mode,
        intent=execution_plan.intent.intent_type,
        execution_plan=execution_plan,
        engineering=engineering,
        message=last_message,
        workflow=workflow_payload,
    )


def stream_task_core(
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
    stream_project_status_fn,
    stream_review_or_fix_fn,
    stream_debug_project_fn,
    stream_generate_driver_fn,
    stream_forge_project_fn,
    build_task_result: Callable[..., dict],
    build_explain_message: Callable[..., str],
    workflow_name_from_execution_plan: Callable[[Any], str],
) -> Generator[dict, None, dict]:
    if execution_plan.missing_info_questions:
        return build_task_result(
            success=False,
            mode="plan",
            intent=execution_plan.intent.intent_type,
            execution_plan=execution_plan,
            engineering=engineering,
            message=execution_plan.missing_info_questions[0],
        )

    legacy_intent = execution_plan.intent.legacy_intent_type or execution_plan.intent.intent_type
    if legacy_intent == "explain":
        return build_task_result(
            success=True,
            mode="explain",
            intent=execution_plan.intent.intent_type,
            execution_plan=execution_plan,
            engineering=engineering,
            message=build_explain_message(task=task, engineering=engineering),
        )

    workflow_name = workflow_name_from_execution_plan(execution_plan)
    dispatch = {
        "project_status": lambda: stream_project_status_fn(
            workspace_root=workspace_root,
            project_name=project_name,
            execution_plan=execution_plan,
            workflow_name=workflow_name,
        ),
        "review_or_fix": lambda: stream_review_or_fix_fn(
            config=config,
            workspace_root=workspace_root,
            task=task,
            project_name=project_name,
            execution_plan=execution_plan,
        ),
        "debug_project": lambda: stream_debug_project_fn(
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
        ),
        "generate_driver": lambda: stream_generate_driver_fn(
            config=config,
            project_root=project_root,
            task=task,
            engineering=engineering,
            execution_plan=execution_plan,
            dry_run=dry_run,
            plan_only=plan_only,
        ),
        "forge_project": lambda: stream_forge_project_fn(
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
        ),
    }
    handler = dispatch.get(legacy_intent)
    if handler is not None:
        return (yield from handler())

    return build_task_result(
        success=True,
        mode="plan",
        intent=execution_plan.intent.intent_type,
        execution_plan=execution_plan,
        engineering=engineering,
        message="任务已进入计划模式。",
    )
