from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.config_manager import AgentConfig
from luxar.core.workflow_engine import WorkflowEngine
from luxar.models.schemas import (
    BuildResult,
    DebugLoopResult,
    DebugRecoveryEvent,
    DriverGenerationResult,
    DriverMetadata,
    DriverPipelineResult,
    FlashResult,
    MonitorResult,
    ReviewReport,
)


class WorkflowEngineTests(unittest.TestCase):
    def test_driver_workflow_maps_pipeline_result_to_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = WorkflowEngine(config=AgentConfig(), project_root=tmpdir)
            pipeline_result = DriverPipelineResult(
                success=True,
                chip="BMI270",
                interface="SPI",
                generated_files=[str(Path(tmpdir) / "bmi270.c")],
                generation_result=DriverGenerationResult(
                    success=True,
                    chip="BMI270",
                    interface="SPI",
                    output_dir=tmpdir,
                    header_path=str(Path(tmpdir) / "bmi270.h"),
                    source_path=str(Path(tmpdir) / "bmi270.c"),
                ),
                review_report=ReviewReport(
                    passed=True,
                    total_issues=0,
                    critical_count=0,
                    error_count=0,
                    warning_count=0,
                    issues=[],
                ),
                fix_iterations=0,
                stored=True,
                stored_records=[
                    DriverMetadata(
                        name="bmi270",
                        protocol="SPI",
                        chip="BMI270",
                        path=str(Path(tmpdir) / "bmi270.c"),
                    )
                ],
            )

            with mock.patch(
                "luxar.core.workflow_engine.LangGraphDriverWorkflow.run",
                return_value=pipeline_result,
            ):
                result = engine.run_driver_workflow(
                    chip="BMI270",
                    interface="SPI",
                    doc_summary="summary",
                )

            self.assertTrue(result.success)
            self.assertEqual("driver", result.workflow)
            self.assertEqual(["retrieve", "decide", "generate", "review", "fix", "store", "skill"], [step.name for step in result.steps])
            self.assertEqual("completed", result.steps[-1].status)
            self.assertIn(result.backend, {"langgraph", "pipeline"})

    def test_debug_workflow_maps_debug_loop_result_to_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = WorkflowEngine(config=AgentConfig(), project_root=tmpdir)
            debug_result = DebugLoopResult(
                success=True,
                stage="complete",
                diagnosis="Debug loop completed successfully.",
                build_result=BuildResult(success=True, return_code=0),
                flash_result=FlashResult(success=True, return_code=0),
                monitor_result=MonitorResult(success=True, port="COM3", lines=["Hello Agent"]),
                recovery_events=[
                    DebugRecoveryEvent(
                        phase="build",
                        action_kind="fix",
                        message="Applied build-aware code fix to app_main.c based on compiler diagnostics.",
                        attempt=2,
                    )
                ],
                build_fix_files=[str(Path(tmpdir) / "app_main.c")],
                build_fix_review_report=ReviewReport(
                    passed=True,
                    total_issues=0,
                    critical_count=0,
                    error_count=0,
                    warning_count=0,
                    issues=[],
                ),
                build_attempts=1,
                flash_attempts=1,
                monitor_attempts=1,
                snapshot_path=str(Path(tmpdir) / ".agent_backups" / "snap"),
                log_dir=str(Path(tmpdir) / "logs"),
            )

            with mock.patch(
                "luxar.core.workflow_engine.LangGraphDebugWorkflow.run",
                return_value=debug_result,
            ):
                result = engine.run_debug_workflow(
                    project_path=str(Path(tmpdir) / "DemoProject"),
                    port="COM3",
                )

            self.assertTrue(result.success)
            self.assertEqual("debug", result.workflow)
            self.assertEqual(["build", "flash", "monitor", "build_fix", "build_fix_review"], [step.name for step in result.steps])
            self.assertEqual("completed", result.steps[0].status)
            self.assertIn(result.backend, {"langgraph", "pipeline"})


if __name__ == "__main__":
    unittest.main()

