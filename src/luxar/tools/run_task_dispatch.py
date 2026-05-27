from __future__ import annotations

from typing import Generator

from luxar.core.config_manager import AgentConfig
from luxar.models.schemas import EngineeringContext
from luxar.tools.run_task_workflows import (
    stream_debug_project as _stream_debug_project_impl,
    stream_forge_project as _stream_forge_project_impl,
    stream_generate_driver as _stream_generate_driver_impl,
    stream_project_status as _stream_project_status_impl,
    stream_review_or_fix as _stream_review_or_fix_impl,
    stream_task_core as _stream_task_core_impl,
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
    project_manager_cls,
    runtime_adapters_module,
    stream_event,
    build_task_result,
    auto_fix_rule_ids,
    should_auto_apply_review_fixes,
    auto_fix_review_files,
    build_review_message,
    infer_driver_request,
    should_run_build_only,
    build_build_message,
    build_explain_message,
    workflow_name_from_execution_plan,
) -> Generator[dict, None, dict]:
    def _stream_project_status(*, workflow_name: str, **_ignored) -> Generator[dict, None, dict]:
        return (yield from _stream_project_status_impl(
            workspace_root=workspace_root,
            project_name=project_name,
            execution_plan=execution_plan,
            workflow_name=workflow_name,
            project_manager_cls=project_manager_cls,
            stream_event=stream_event,
            build_task_result=build_task_result,
        ))

    def _stream_review_or_fix(**_ignored) -> Generator[dict, None, dict]:
        return (yield from _stream_review_or_fix_impl(
            config=config,
            workspace_root=workspace_root,
            task=task,
            project_name=project_name,
            execution_plan=execution_plan,
            project_manager_cls=project_manager_cls,
            runtime_adapters_module=runtime_adapters_module,
            stream_event=stream_event,
            build_task_result=build_task_result,
            auto_fix_rule_ids=auto_fix_rule_ids,
            should_auto_apply_review_fixes=should_auto_apply_review_fixes,
            auto_fix_review_files=auto_fix_review_files,
            build_review_message=build_review_message,
        ))

    def _stream_generate_driver(**_ignored) -> Generator[dict, None, dict]:
        return (yield from _stream_generate_driver_impl(
            config=config,
            project_root=project_root,
            task=task,
            engineering=engineering,
            execution_plan=execution_plan,
            dry_run=dry_run,
            plan_only=plan_only,
            runtime_adapters_module=runtime_adapters_module,
            stream_event=stream_event,
            build_task_result=build_task_result,
            infer_driver_request=infer_driver_request,
        ))

    def _stream_debug_project(**_ignored) -> Generator[dict, None, dict]:
        return (yield from _stream_debug_project_impl(
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
            project_manager_cls=project_manager_cls,
            runtime_adapters_module=runtime_adapters_module,
            stream_event=stream_event,
            build_task_result=build_task_result,
            should_run_build_only=should_run_build_only,
            build_build_message=build_build_message,
        ))

    def _stream_forge_project(**_ignored) -> Generator[dict, None, dict]:
        return (yield from _stream_forge_project_impl(
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
            project_manager_cls=project_manager_cls,
            runtime_adapters_module=runtime_adapters_module,
            stream_event=stream_event,
            build_task_result=build_task_result,
        ))

    return (yield from _stream_task_core_impl(
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
        stream_project_status_fn=_stream_project_status,
        stream_review_or_fix_fn=_stream_review_or_fix,
        stream_debug_project_fn=_stream_debug_project,
        stream_generate_driver_fn=_stream_generate_driver,
        stream_forge_project_fn=_stream_forge_project,
        build_task_result=build_task_result,
        build_explain_message=build_explain_message,
        workflow_name_from_execution_plan=workflow_name_from_execution_plan,
    ))
