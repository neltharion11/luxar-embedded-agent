from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.config_manager import AgentConfig
from luxar.core.debug_loop import DebugLoop
from luxar.models.schemas import BuildResult, DebugLoopResult, MonitorResult, ReviewReport
from luxar.workflows.debug_graph import LangGraphDebugWorkflow


class DebugGraphTests(unittest.TestCase):
    def test_falls_back_to_debug_loop_when_langgraph_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_loop = DebugLoop(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDebugWorkflow(debug_loop)
            expected = DebugLoopResult(
                success=False,
                stage="build",
                diagnosis="Build failed.",
                build_result=BuildResult(success=False, return_code=1),
                snapshot_path=str(Path(tmpdir) / ".agent_backups" / "snap"),
                log_dir=str(Path(tmpdir) / "logs"),
            )

            with mock.patch("luxar.workflows.debug_graph.LANGGRAPH_AVAILABLE", False), mock.patch.object(
                debug_loop,
                "run",
                return_value=expected,
            ) as run_mock:
                result = workflow.run(
                    project_path=str(Path(tmpdir) / "DemoProject"),
                    port="COM3",
                )

            self.assertFalse(result.success)
            self.assertEqual("build", result.stage)
            run_mock.assert_called_once()

    def test_monitor_retry_updates_attempts_and_recovery_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_loop = DebugLoop(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDebugWorkflow(debug_loop)
            state = {
                "context": {"project": Path(tmpdir), "logger": mock.Mock(), "snapshot_path": "", "log_dir": ""},
                "port": "COM3",
                "baudrate": 115200,
                "timeout": 2,
                "lines": 3,
                "monitor_attempts": 0,
                "recovery_actions": [],
            }
            with mock.patch.object(
                debug_loop,
                "_run_monitor",
                return_value=MonitorResult(success=False, port="COM3", error="No serial data captured within timeout."),
            ):
                updated = workflow._monitor_node(state)
            self.assertEqual(1, updated["monitor_attempts"])
            next_step = workflow._after_monitor({**state, **updated})
            self.assertEqual("recover_monitor", next_step)
            recovered = workflow._recover_monitor_node({**state, **updated})
            self.assertTrue(recovered["recovery_actions"])
            self.assertEqual("monitor", recovered["recovery_events"][0].phase)
            self.assertEqual("retry", recovered["recovery_events"][0].action_kind)

    def test_build_toolchain_missing_does_not_retry_clean_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_loop = DebugLoop(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDebugWorkflow(debug_loop)
            decision = workflow._after_build(
                {
                    "build_result": BuildResult(success=False, stderr="arm-none-eabi-gcc: not found"),
                    "build_attempts": 1,
                    "clean": False,
                    "build_failure_type": debug_loop._classify_build_failure("arm-none-eabi-gcc: not found"),
                }
            )
            self.assertEqual("end", decision)

    def test_flash_target_identification_failure_uses_explicit_probe_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_loop = DebugLoop(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDebugWorkflow(debug_loop)
            recovered = workflow._recover_flash_node(
                {
                    "flash_failure_type": "target_not_identified",
                    "recovery_actions": [],
                    "probe": None,
                }
            )
            self.assertEqual(debug_loop.config.flash.default_probe, recovered["probe"])
            self.assertTrue(recovered["recovery_actions"])

    def test_compile_error_prefers_build_fix_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_loop = DebugLoop(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDebugWorkflow(debug_loop)
            decision = workflow._after_build(
                {
                    "build_result": BuildResult(success=False, stderr="main.c:10:5: error: expected ';' before '}' token"),
                    "build_attempts": 1,
                    "clean": False,
                    "build_failure_type": "compile_error",
                }
            )
            self.assertEqual("recover_build_fix", decision)

    def test_link_error_prefers_link_repair_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_loop = DebugLoop(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDebugWorkflow(debug_loop)
            decision = workflow._after_build(
                {
                    "build_result": BuildResult(success=False, stderr="ld: undefined reference to `Reset_Handler'"),
                    "build_attempts": 1,
                    "clean": False,
                    "build_failure_type": "link_error",
                }
            )
            self.assertEqual("recover_build_link", decision)

    def test_build_fix_recovery_records_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_loop = DebugLoop(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDebugWorkflow(debug_loop)
            with mock.patch.object(
                debug_loop,
                "_attempt_build_fix",
                return_value={
                    "actions": ["Applied build-aware code fix to app_main.c based on compiler diagnostics."],
                    "fixed_files": [str(Path(tmpdir) / "App" / "Src" / "app_main.c")],
                },
            ):
                recovered = workflow._recover_build_fix_node(
                    {
                        "context": {"project": Path(tmpdir)},
                        "build_result": BuildResult(success=False, stderr="App/Src/app_main.c:1:1: error: bad"),
                        "recovery_actions": [],
                    }
                )
            self.assertTrue(recovered["recovery_actions"])
            self.assertTrue(recovered["build_fix_files"])
            self.assertEqual("build", recovered["recovery_events"][0].phase)
            self.assertEqual("fix", recovered["recovery_events"][0].action_kind)

    def test_build_fix_review_passes_then_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_loop = DebugLoop(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDebugWorkflow(debug_loop)
            report = ReviewReport(
                passed=True,
                total_issues=0,
                critical_count=0,
                error_count=0,
                warning_count=0,
                issues=[],
            )
            with mock.patch.object(debug_loop, "_review_fixed_files", return_value=report):
                reviewed = workflow._review_build_fix_node(
                    {
                        "context": {"project": Path(tmpdir), "logger": mock.Mock()},
                        "build_fix_files": [str(Path(tmpdir) / "App" / "Src" / "app_main.c")],
                    }
                )
            self.assertTrue(reviewed["build_fix_review_report"].passed)
            self.assertEqual("build", workflow._after_build_fix_review(reviewed))

    def test_build_fix_review_failure_blocks_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_loop = DebugLoop(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDebugWorkflow(debug_loop)
            report = ReviewReport(
                passed=False,
                total_issues=1,
                critical_count=0,
                error_count=1,
                warning_count=0,
                issues=[],
            )
            with mock.patch.object(debug_loop, "_review_fixed_files", return_value=report):
                reviewed = workflow._review_build_fix_node(
                    {
                        "context": {"project": Path(tmpdir), "logger": mock.Mock()},
                        "build_fix_files": [str(Path(tmpdir) / "App" / "Src" / "app_main.c")],
                    }
                )
            self.assertFalse(reviewed["build_fix_review_report"].passed)
            self.assertEqual("end", workflow._after_build_fix_review(reviewed))

    def test_link_repair_records_actions_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            debug_loop = DebugLoop(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDebugWorkflow(debug_loop)
            with mock.patch.object(
                debug_loop,
                "_attempt_link_repair",
                return_value={
                    "actions": ["Restored missing STM32 startup/runtime scaffold files before retrying the link step."],
                    "fixed_files": [str(Path(tmpdir) / "Core" / "Src" / "startup_stm32.s")],
                },
            ):
                recovered = workflow._recover_build_link_node(
                    {
                        "context": {"project": Path(tmpdir)},
                        "build_result": BuildResult(success=False, stderr="ld: undefined reference to `Reset_Handler'"),
                        "recovery_actions": [],
                    }
                )
            self.assertTrue(recovered["recovery_actions"])
            self.assertTrue(recovered["build_fix_files"])
            self.assertEqual("build", recovered["recovery_events"][0].phase)
            self.assertEqual("fix", recovered["recovery_events"][0].action_kind)


if __name__ == "__main__":
    unittest.main()

