from __future__ import annotations

from luxar.agent.workers import runtime_adapters
from luxar.core.project_manager import ProjectManager


def build_stream_entrypoint_dependencies(
    *,
    prepare_legacy_task_invocation,
    build_workflow_started_event,
    dispatch_stream_task_core,
    stream_legacy_task_result,
    project_manager_cls=None,
    runtime_adapters_module=None,
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
    prepare_task_execution,
    workflow_name_from_execution_plan,
) -> dict:
    project_manager_cls = project_manager_cls or ProjectManager
    runtime_adapters_module = runtime_adapters_module or runtime_adapters
    return {
        "prepare_legacy_task_invocation": prepare_legacy_task_invocation,
        "build_workflow_started_event": build_workflow_started_event,
        "dispatch_stream_task_core": dispatch_stream_task_core,
        "stream_legacy_task_result": stream_legacy_task_result,
        "project_manager_cls": project_manager_cls,
        "runtime_adapters_module": runtime_adapters_module,
        "stream_event": stream_event,
        "build_task_result": build_task_result,
        "auto_fix_rule_ids": auto_fix_rule_ids,
        "should_auto_apply_review_fixes": should_auto_apply_review_fixes,
        "auto_fix_review_files": auto_fix_review_files,
        "build_review_message": build_review_message,
        "infer_driver_request": infer_driver_request,
        "should_run_build_only": should_run_build_only,
        "build_build_message": build_build_message,
        "build_explain_message": build_explain_message,
        "prepare_task_execution": prepare_task_execution,
        "workflow_name_from_execution_plan": workflow_name_from_execution_plan,
    }


def build_sync_entrypoint_dependencies(
    *,
    prepare_legacy_task_invocation,
    consume_stream_result,
    dispatch_stream_task_core,
    project_manager_cls=None,
    runtime_adapters_module=None,
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
    prepare_task_execution,
    workflow_name_from_execution_plan,
) -> dict:
    project_manager_cls = project_manager_cls or ProjectManager
    runtime_adapters_module = runtime_adapters_module or runtime_adapters
    return {
        "prepare_legacy_task_invocation": prepare_legacy_task_invocation,
        "consume_stream_result": consume_stream_result,
        "dispatch_stream_task_core": dispatch_stream_task_core,
        "project_manager_cls": project_manager_cls,
        "runtime_adapters_module": runtime_adapters_module,
        "stream_event": stream_event,
        "build_task_result": build_task_result,
        "auto_fix_rule_ids": auto_fix_rule_ids,
        "should_auto_apply_review_fixes": should_auto_apply_review_fixes,
        "auto_fix_review_files": auto_fix_review_files,
        "build_review_message": build_review_message,
        "infer_driver_request": infer_driver_request,
        "should_run_build_only": should_run_build_only,
        "build_build_message": build_build_message,
        "build_explain_message": build_explain_message,
        "prepare_task_execution": prepare_task_execution,
        "workflow_name_from_execution_plan": workflow_name_from_execution_plan,
    }
