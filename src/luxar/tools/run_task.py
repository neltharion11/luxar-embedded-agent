from __future__ import annotations

from typing import Iterator

from luxar.core.config_manager import AgentConfig
from luxar.tools.run_task_compat import (
    LEGACY_COMPATIBILITY_MODE,
    LEGACY_RUN_TASK_WARNING,
    build_task_result as _build_task_result,
    build_workflow_started_event as _build_workflow_started_event,
    consume_stream_result as _consume_stream_result,
    prepare_legacy_task_invocation as _prepare_legacy_task_invocation,
    stream_event as _stream_event,
    stream_legacy_task_result as _stream_legacy_task_result,
    workflow_name_from_execution_plan as _workflow_name_from_execution_plan,
)
from luxar.tools.run_task_dispatch import stream_task_core as _dispatch_stream_task_core
from luxar.tools.run_task_dependencies import (
    build_stream_entrypoint_dependencies as _build_stream_entrypoint_dependencies,
    build_sync_entrypoint_dependencies as _build_sync_entrypoint_dependencies,
)
from luxar.tools.run_task_entrypoints import (
    run_task_entrypoint as _run_task_entrypoint,
    run_task_stream_entrypoint as _run_task_stream_entrypoint,
)
from luxar.tools.run_task_support import (
    auto_fix_review_files as _auto_fix_review_files,
    auto_fix_rule_ids as _auto_fix_rule_ids,
    build_build_message as _build_build_message,
    build_explain_message as _build_explain_message,
    build_review_message as _build_review_message,
    infer_driver_request as _infer_driver_request,
    prepare_task_execution as _prepare_task_execution,
    should_auto_apply_review_fixes as _should_auto_apply_review_fixes,
    should_run_build_only as _should_run_build_only,
)

RETAINED_LEGACY_RUN_TASK_EXPORTS = (
    "LEGACY_COMPATIBILITY_MODE",
    "LEGACY_RUN_TASK_WARNING",
    "run_task",
    "run_task_stream",
)

PUBLIC_RUN_TASK_EXPORTS = (
    *RETAINED_LEGACY_RUN_TASK_EXPORTS,
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
    yield from _run_task_stream_entrypoint(
        config=config,
        project_root=project_root,
        workspace_root=workspace_root,
        driver_library_root=driver_library_root,
        task=task,
        project_name=project_name,
        docs=docs,
        dry_run=dry_run,
        plan_only=plan_only,
        no_build=no_build,
        no_flash=no_flash,
        no_monitor=no_monitor,
        **_build_stream_entrypoint_dependencies(
            prepare_legacy_task_invocation=_prepare_legacy_task_invocation,
            build_workflow_started_event=_build_workflow_started_event,
            dispatch_stream_task_core=_dispatch_stream_task_core,
            stream_legacy_task_result=_stream_legacy_task_result,
            stream_event=_stream_event,
            build_task_result=_build_task_result,
            auto_fix_rule_ids=_auto_fix_rule_ids,
            should_auto_apply_review_fixes=_should_auto_apply_review_fixes,
            auto_fix_review_files=_auto_fix_review_files,
            build_review_message=_build_review_message,
            infer_driver_request=_infer_driver_request,
            should_run_build_only=_should_run_build_only,
            build_build_message=_build_build_message,
            build_explain_message=_build_explain_message,
            prepare_task_execution=_prepare_task_execution,
            workflow_name_from_execution_plan=_workflow_name_from_execution_plan,
        ),
    )


__all__ = PUBLIC_RUN_TASK_EXPORTS


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
    return _run_task_entrypoint(
        config=config,
        project_root=project_root,
        workspace_root=workspace_root,
        driver_library_root=driver_library_root,
        task=task,
        project_name=project_name,
        docs=docs,
        dry_run=dry_run,
        plan_only=plan_only,
        no_build=no_build,
        no_flash=no_flash,
        no_monitor=no_monitor,
        **_build_sync_entrypoint_dependencies(
            prepare_legacy_task_invocation=_prepare_legacy_task_invocation,
            consume_stream_result=_consume_stream_result,
            dispatch_stream_task_core=_dispatch_stream_task_core,
            stream_event=_stream_event,
            build_task_result=_build_task_result,
            auto_fix_rule_ids=_auto_fix_rule_ids,
            should_auto_apply_review_fixes=_should_auto_apply_review_fixes,
            auto_fix_review_files=_auto_fix_review_files,
            build_review_message=_build_review_message,
            infer_driver_request=_infer_driver_request,
            should_run_build_only=_should_run_build_only,
            build_build_message=_build_build_message,
            build_explain_message=_build_explain_message,
            prepare_task_execution=_prepare_task_execution,
            workflow_name_from_execution_plan=_workflow_name_from_execution_plan,
        ),
    )
