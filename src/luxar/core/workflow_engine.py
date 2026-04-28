from __future__ import annotations

from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.debug_loop import DebugLoop
from luxar.core.driver_pipeline import DriverPipeline
from luxar.core.skill_manager import SkillManager
from luxar.models.schemas import (
    DebugLoopResult,
    DriverPipelineResult,
    WorkflowRunResult,
    WorkflowStepResult,
)
from luxar.workflows.debug_graph import LangGraphDebugWorkflow
from luxar.workflows.driver_graph import LANGGRAPH_AVAILABLE, LangGraphDriverWorkflow


class WorkflowEngine:
    def __init__(self, config: AgentConfig, project_root: str):
        self.config = config
        self.project_root = Path(project_root).resolve()

    def run_driver_workflow(
        self,
        chip: str,
        interface: str,
        doc_summary: str,
        register_summary: str = "",
        vendor: str = "",
        device: str = "",
        output_dir: str = "",
        max_fix_iterations: int | None = None,
    ) -> WorkflowRunResult:
        pipeline = DriverPipeline(config=self.config, project_root=str(self.project_root))
        workflow = LangGraphDriverWorkflow(pipeline)
        result = workflow.run(
            chip=chip,
            interface=interface,
            protocol_summary=doc_summary,
            register_summary=register_summary,
            vendor=vendor,
            device=device,
            output_dir=output_dir,
            max_fix_iterations=max_fix_iterations,
        )
        return self._driver_result_to_workflow(
            result,
            backend="langgraph" if LANGGRAPH_AVAILABLE else "pipeline",
            source_project=device or chip,
        )

    def run_debug_workflow(
        self,
        project_path: str,
        probe: str | None = None,
        port: str = "",
        clean: bool = False,
        lines: int = 10,
        baudrate: int | None = None,
    ) -> WorkflowRunResult:
        debug_loop = DebugLoop(config=self.config, project_root=str(self.project_root))
        workflow = LangGraphDebugWorkflow(debug_loop)
        result = workflow.run(
            project_path=project_path,
            probe=probe,
            port=port,
            clean=clean,
            lines=lines,
            baudrate=baudrate,
        )
        return self._debug_result_to_workflow(
            result,
            backend="langgraph" if LANGGRAPH_AVAILABLE else "pipeline",
        )

    def _driver_result_to_workflow(
        self,
        result: DriverPipelineResult,
        backend: str = "pipeline",
        source_project: str = "",
    ) -> WorkflowRunResult:
        steps: list[WorkflowStepResult] = []
        generation_payload = result.generation_result.model_dump(mode="json") if result.generation_result is not None else {}
        steps.append(
            WorkflowStepResult(
                name="retrieve",
                status="completed" if generation_payload.get("reuse_summary") or generation_payload.get("reuse_sources") else "skipped",
                message="Collected local reuse context." if generation_payload.get("reuse_summary") or generation_payload.get("reuse_sources") else "No local reuse context was available.",
                payload={
                    "reuse_summary": generation_payload.get("reuse_summary", ""),
                    "reuse_sources": generation_payload.get("reuse_sources", []),
                },
            )
        )
        reused_existing = bool(result.generation_result and result.generation_result.reused_existing)
        steps.append(
            WorkflowStepResult(
                name="decide",
                status="completed" if result.generation_result is not None else "skipped",
                message="Selected existing reviewed driver for reuse." if reused_existing else "Selected fresh generation path.",
                payload={
                    "reused_existing": reused_existing,
                    "reused_driver_path": generation_payload.get("reused_driver_path", ""),
                },
            )
        )
        generation_success = bool(result.generation_result and result.generation_result.success)
        steps.append(
            WorkflowStepResult(
                name="reuse" if reused_existing else "generate",
                status="completed" if generation_success else "failed",
                message="" if generation_success else (result.error or "Driver generation failed."),
                payload=generation_payload,
            )
        )

        review_payload = result.review_report.model_dump(mode="json") if result.review_report is not None else {}
        review_status = "completed" if result.review_report and result.review_report.passed else "failed"
        if result.review_report is None:
            review_status = "skipped"
        steps.append(
            WorkflowStepResult(
                name="review",
                status=review_status,
                message="" if review_status == "completed" else (result.error or "Driver review did not pass."),
                payload=review_payload,
            )
        )

        if result.fix_iterations > 0:
            steps.append(
                WorkflowStepResult(
                    name="fix",
                    status="completed" if result.success else "failed",
                    message=f"Applied {result.fix_iterations} fix iteration(s).",
                    payload={
                        "fix_iterations": result.fix_iterations,
                        "fixed_files": result.fixed_files,
                    },
                )
            )
        else:
            steps.append(
                WorkflowStepResult(
                    name="fix",
                    status="skipped",
                    message="No automatic fix iteration was needed.",
                    payload={"fix_iterations": 0, "fixed_files": []},
                )
            )

        steps.append(
            WorkflowStepResult(
                name="store",
                status="completed" if result.stored else ("skipped" if not result.success else "failed"),
                message=(
                    f"Stored {len(result.stored_records)} driver record(s)."
                    if result.stored
                    else ("Driver was not stored because the workflow did not pass review." if not result.success else "Driver storage did not complete.")
                ),
                payload={"records": [item.model_dump(mode="json") for item in result.stored_records]},
            )
        )

        skill_artifact = None
        skill_status = "skipped"
        skill_message = "Skill update conditions were not met."
        if result.review_report is not None:
            skill_manager = SkillManager(self.config, str(self.project_root))
            should_update = skill_manager.should_update_protocol_skill(
                review_passed=result.review_report.passed,
                build_success=result.success,
                project_success=result.success,
            )
            if should_update and result.success:
                lessons = [issue.message for issue in result.review_report.issues[:5]]
                skill_artifact = skill_manager.update_protocol_skill(
                    protocol=result.interface,
                    device_name=result.chip,
                    summary=result.generation_result.raw_response if result.generation_result else "",
                    lessons_learned=lessons,
                    platforms=[self.config.platform.default_platform],
                    runtimes=[self.config.platform.default_runtime],
                    source_project=source_project or result.chip,
                )
                result.skill_artifact = skill_artifact
                skill_status = "completed"
                skill_message = "Protocol skill updated."
            elif result.success:
                skill_message = "Protocol skill update is disabled by configuration."
        steps.append(
            WorkflowStepResult(
                name="skill",
                status=skill_status,
                message=skill_message,
                payload=skill_artifact.model_dump(mode="json") if skill_artifact else {},
            )
        )

        summary = "Driver workflow completed successfully." if result.success else (result.error or "Driver workflow failed.")
        return WorkflowRunResult(
            success=result.success,
            workflow="driver",
            steps=steps,
            summary=summary,
            backend=backend,
            output=result.model_dump(mode="json"),
        )

    def _debug_result_to_workflow(self, result: DebugLoopResult, backend: str = "pipeline") -> WorkflowRunResult:
        steps: list[WorkflowStepResult] = []
        if result.build_result is not None:
            steps.append(
                WorkflowStepResult(
                    name="build",
                    status="completed" if result.build_result.success else "failed",
                    message="" if result.build_result.success else result.diagnosis,
                    payload={
                        **result.build_result.model_dump(mode="json"),
                        "attempts": result.build_attempts,
                    },
                )
            )
        if result.flash_result is not None:
            steps.append(
                WorkflowStepResult(
                    name="flash",
                    status="completed" if result.flash_result.success else "failed",
                    message="" if result.flash_result.success else result.diagnosis,
                    payload={
                        **result.flash_result.model_dump(mode="json"),
                        "attempts": result.flash_attempts,
                    },
                )
            )
        if result.monitor_result is not None:
            steps.append(
                WorkflowStepResult(
                    name="monitor",
                    status="completed" if result.monitor_result.success else "failed",
                    message="" if result.monitor_result.success else result.diagnosis,
                    payload={
                        **result.monitor_result.model_dump(mode="json"),
                        "attempts": result.monitor_attempts,
                    },
                )
            )
        for event in result.recovery_events:
            step_name = f"{event.phase}_{event.action_kind}"
            steps.append(
                WorkflowStepResult(
                    name=step_name,
                    status="completed",
                    message=event.message,
                    payload={
                        "phase": event.phase,
                        "action_kind": event.action_kind,
                        "attempt": event.attempt,
                    },
                )
            )
        if result.build_fix_review_report is not None:
            steps.append(
                WorkflowStepResult(
                    name="build_fix_review",
                    status="completed" if result.build_fix_review_report.passed else "failed",
                    message=(
                        "Post-fix review passed."
                        if result.build_fix_review_report.passed
                        else "Post-fix review blocked rebuild."
                    ),
                    payload={
                        **result.build_fix_review_report.model_dump(mode="json"),
                        "fixed_files": result.build_fix_files,
                    },
                )
            )
        if result.recovery_actions and not result.recovery_events:
            steps.append(
                WorkflowStepResult(
                    name="recover",
                    status="completed",
                    message="Applied automatic recovery actions during debug workflow.",
                    payload={"actions": result.recovery_actions},
                )
            )
        return WorkflowRunResult(
            success=result.success,
            workflow="debug",
            steps=steps,
            summary=result.diagnosis,
            backend=backend,
            output=result.model_dump(mode="json"),
        )

