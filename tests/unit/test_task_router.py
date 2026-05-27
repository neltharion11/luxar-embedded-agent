from __future__ import annotations

import shutil
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from luxar.core.config_manager import AgentConfig
from luxar.core.task_router import LEGACY_TASK_ROUTER_COMPATIBILITY_MODE, TaskRouter
from luxar.models.schemas import BuildResult, DriverPipelineResult, ProjectConfig, WorkflowRunResult
from luxar.tools.run_task import (
    LEGACY_COMPATIBILITY_MODE,
    LEGACY_RUN_TASK_WARNING,
    run_task,
    run_task_stream,
)


class TaskRouterTests(unittest.TestCase):
    def test_explain_request_routes_to_explain(self) -> None:
        plan = TaskRouter().route(task="Explain the BMI270 SPI frame format.", docs=["workspace/docs/bmi270.pdf"])
        self.assertEqual("runtime_explain", plan.intent.intent_type)
        self.assertEqual("runtime_explain", plan.intent.public_intent)
        self.assertEqual("explain", plan.intent.legacy_intent_type)
        self.assertEqual(LEGACY_TASK_ROUTER_COMPATIBILITY_MODE, plan.intent.compatibility_mode)
        self.assertEqual(LEGACY_TASK_ROUTER_COMPATIBILITY_MODE, plan.compatibility_mode)

    def test_generate_project_routes_to_forge(self) -> None:
        plan = TaskRouter().route(task="Generate a project that blinks LED and prints UART logs.", project="DirectF1C")
        self.assertEqual("runtime_run", plan.intent.intent_type)
        self.assertEqual("runtime_run", plan.intent.public_intent)
        self.assertEqual("runtime_run", plan.intent.public_path)
        self.assertEqual("forge_project", plan.intent.legacy_intent_type)

    def test_fix_compile_error_routes_to_debug_or_fix(self) -> None:
        plan = TaskRouter().route(task="Fix the compile error and rebuild the project.", project="DirectF1C")
        self.assertEqual("runtime_run", plan.intent.intent_type)
        self.assertEqual("debug_project", plan.intent.legacy_intent_type)

    def test_fix_file_issue_routes_to_review_or_fix(self) -> None:
        plan = TaskRouter().route(task="修复 app_main.c 里的 printf 和注释问题", project="DirectF1C")
        self.assertEqual("runtime_run", plan.intent.intent_type)
        self.assertEqual("review_or_fix", plan.intent.legacy_intent_type)

    def test_docs_push_task_toward_analysis(self) -> None:
        plan = TaskRouter().route(task="Help me wire this device.", docs=["workspace/docs/bmi270.pdf"])
        self.assertIn(plan.intent.intent_type, {"runtime_run", "runtime_explain"})
        self.assertTrue(plan.steps[0] in {"parse_docs", "analyze_docs"})

    def test_pin_heavy_project_execution_request_routes_to_forge(self) -> None:
        plan = TaskRouter().route(
            task="PA6 PA7 PB0 是 RGB 灯引脚，直接执行并生成完整 RGB 彩虹灯工程代码",
            project="manualtest",
        )
        self.assertEqual("runtime_run", plan.intent.intent_type)
        self.assertEqual("forge_project", plan.intent.legacy_intent_type)

    def test_plain_pin_explanation_still_routes_to_explain(self) -> None:
        plan = TaskRouter().route(task="解释一下 PA6 PA7 PB0 分别接什么")
        self.assertEqual("runtime_explain", plan.intent.intent_type)
        self.assertEqual("explain", plan.intent.legacy_intent_type)

    def test_pin_request_with_code_generation_signal_routes_to_forge(self) -> None:
        plan = TaskRouter().route(
            task="引脚 PA6 PA7 PB0 已知，请生成代码并创建完整工程",
            project="manualtest",
        )
        self.assertEqual("runtime_run", plan.intent.intent_type)
        self.assertEqual("forge_project", plan.intent.legacy_intent_type)


class RunTaskTests(unittest.TestCase):
    def test_run_task_emits_legacy_compatibility_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config = AgentConfig()
            run_task(
                config=config,
                project_root=tmpdir,
                workspace_root=tmpdir,
                driver_library_root=str(Path(tmpdir) / "driver_library"),
                task="你好",
            )
        self.assertTrue(any(LEGACY_RUN_TASK_WARNING in str(item.message) for item in caught))

    def test_run_task_greeting_returns_human_friendly_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AgentConfig()
            result = run_task(
                config=config,
                project_root=tmpdir,
                workspace_root=tmpdir,
                driver_library_root=str(Path(tmpdir) / "driver_library"),
                task="你好",
            )
        self.assertTrue(result["success"])
        self.assertEqual("explain", result["mode"])
        self.assertIn("你好", result["message"])
        self.assertEqual("runtime_explain", result["intent"])
        self.assertEqual("explain", result["legacy_intent"])
        self.assertIn("artifacts", result)
        self.assertEqual(LEGACY_COMPATIBILITY_MODE, result["compatibility_mode"])
        self.assertEqual(LEGACY_RUN_TASK_WARNING, result["legacy_warning"])

    def test_run_task_capability_question_returns_natural_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AgentConfig()
            result = run_task(
                config=config,
                project_root=tmpdir,
                workspace_root=tmpdir,
                driver_library_root=str(Path(tmpdir) / "driver_library"),
                task="你有什么功能",
            )
        self.assertTrue(result["success"])
        self.assertEqual("explain", result["mode"])
        self.assertIn("我可以帮你", result["message"])

    def test_run_task_uses_forge_for_project_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AgentConfig()
            workflow = WorkflowRunResult(success=True, workflow="forge")
            with patch("luxar.tools.run_task_dependencies.ProjectManager") as pm_cls, \
                 patch("luxar.tools.run_task_dependencies.runtime_adapters.run_forge", return_value=workflow):
                pm_cls.return_value.load_project.return_value = ProjectConfig(
                    name="DirectF1C",
                    path=str(Path(tmpdir) / "DirectF1C"),
                    project_mode="firmware",
                    mcu="STM32F103C8T6",
                )
                result = run_task(
                    config=config,
                    project_root=tmpdir,
                    workspace_root=tmpdir,
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    task="Generate project that blinks LED and prints UART.",
                    project_name="DirectF1C",
                )
        self.assertTrue(result["success"])
        self.assertEqual("execute", result["mode"])
        self.assertIn("artifacts", result)
        self.assertIn("workflow", result["artifacts"])

    def test_run_task_plan_only_returns_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AgentConfig()
            workflow = WorkflowRunResult(success=True, workflow="forge")
            with patch("luxar.tools.run_task_dependencies.ProjectManager") as pm_cls, \
                 patch("luxar.tools.run_task_dependencies.runtime_adapters.run_forge", return_value=workflow):
                pm_cls.return_value.load_project.return_value = ProjectConfig(
                    name="DirectF1C",
                    path=str(Path(tmpdir) / "DirectF1C"),
                    project_mode="firmware",
                    mcu="STM32F103C8T6",
                )
                result = run_task(
                    config=config,
                    project_root=tmpdir,
                    workspace_root=tmpdir,
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    task="Generate project that blinks LED and prints UART.",
                    project_name="DirectF1C",
                    plan_only=True,
                )
        self.assertTrue(result["success"])
        self.assertEqual("plan", result["mode"])

    def test_run_task_build_request_uses_build_project_not_debug_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AgentConfig()
            build_result = BuildResult(success=True, return_code=0)
            with patch("luxar.tools.run_task_dependencies.ProjectManager") as pm_cls, \
                 patch("luxar.tools.run_task_dependencies.runtime_adapters.run_build", return_value=build_result) as build_mock, \
                 patch("luxar.tools.run_task_dependencies.runtime_adapters.run_debug") as debug_mock:
                pm_cls.return_value.load_project.return_value = ProjectConfig(
                    name="DirectF1C",
                    path=str(Path(tmpdir) / "DirectF1C"),
                    project_mode="firmware",
                    mcu="STM32F103C8T6",
                )
                result = run_task(
                    config=config,
                    project_root=tmpdir,
                    workspace_root=tmpdir,
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    task="编译并构建当前项目",
                    project_name="DirectF1C",
                )
        self.assertTrue(result["success"])
        self.assertIn("构建已经完成并通过", result["message"])
        build_mock.assert_called_once()
        debug_mock.assert_not_called()

    def test_run_task_generate_driver_uses_driver_pipeline_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AgentConfig()
            pipeline = DriverPipelineResult(success=True, chip="BMI270", interface="SPI")
            with patch("luxar.tools.run_task_dependencies.runtime_adapters.run_generate_driver", return_value=pipeline):
                result = run_task(
                    config=config,
                    project_root=tmpdir,
                    workspace_root=tmpdir,
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    task="Generate BMI270 SPI driver",
                )
        self.assertTrue(result["success"])
        self.assertEqual("runtime_run", result["intent"])
        self.assertEqual("generate_driver", result["legacy_intent"])
        self.assertIn("artifacts", result)
        self.assertEqual("BMI270", result["artifacts"]["driver_pipeline"]["chip"])
        self.assertEqual({}, result["artifacts"]["workflow"])

    def test_run_task_stream_final_result_matches_sync_result_for_explain(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AgentConfig()
            sync_result = run_task(
                config=config,
                project_root=tmpdir,
                workspace_root=tmpdir,
                driver_library_root=str(Path(tmpdir) / "driver_library"),
                task="你有什么功能",
            )
            final_event = None
            for event in run_task_stream(
                config=config,
                project_root=tmpdir,
                workspace_root=tmpdir,
                driver_library_root=str(Path(tmpdir) / "driver_library"),
                task="你有什么功能",
            ):
                final_event = event
        self.assertIsNotNone(final_event)
        self.assertEqual("workflow_finished", final_event["type"])
        self.assertEqual(LEGACY_COMPATIBILITY_MODE, final_event["compatibility_mode"])
        self.assertEqual(sync_result, final_event["payload"]["result"])

    def test_run_task_stream_final_result_matches_sync_result_for_driver_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AgentConfig()
            pipeline = DriverPipelineResult(success=True, chip="BMI270", interface="SPI")
            with patch("luxar.tools.run_task_dependencies.runtime_adapters.run_generate_driver", return_value=pipeline):
                sync_result = run_task(
                    config=config,
                    project_root=tmpdir,
                    workspace_root=tmpdir,
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    task="Generate BMI270 SPI driver",
                )
                final_event = None
                for event in run_task_stream(
                    config=config,
                    project_root=tmpdir,
                    workspace_root=tmpdir,
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    task="Generate BMI270 SPI driver",
                ):
                    final_event = event
        self.assertIsNotNone(final_event)
        self.assertEqual("workflow_finished", final_event["type"])
        self.assertEqual(LEGACY_COMPATIBILITY_MODE, final_event["compatibility_mode"])
        self.assertEqual(sync_result, final_event["payload"]["result"])

    def test_run_task_auto_fixes_known_review_issues(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            config = AgentConfig()
            project_path = str(Path(tmpdir) / "DirectF1C")
            issue_file = str(Path(project_path) / "App" / "Src" / "app_main.c")
            first_report = {
                "report": {
                    "passed": False,
                    "total_issues": 2,
                    "critical_count": 0,
                    "error_count": 1,
                    "warning_count": 1,
                    "issues": [
                        {
                            "severity": "error",
                            "rule_id": "EMB-004",
                            "file": issue_file,
                            "line": 10,
                            "message": "Driver code must not use printf.",
                        },
                        {
                            "severity": "warning",
                            "rule_id": "EMB-003",
                            "file": issue_file,
                            "line": 1,
                            "message": "Exported function is missing a Doxygen-style comment.",
                        },
                    ],
                }
            }
            second_report = {
                "report": {
                    "passed": True,
                    "total_issues": 0,
                    "critical_count": 0,
                    "error_count": 0,
                    "warning_count": 0,
                    "issues": [],
                }
            }
            with patch("luxar.tools.run_task_dependencies.ProjectManager") as pm_cls, \
                 patch("luxar.tools.run_task_dependencies.runtime_adapters.run_review", side_effect=[first_report, second_report]), \
                 patch("luxar.tools.run_task_support.RepairWorker.repair_file", return_value={"success": True, "applied": True}) as fix_mock:
                pm_cls.return_value.load_project.return_value = ProjectConfig(
                    name="DirectF1C",
                    path=project_path,
                    project_mode="firmware",
                    mcu="STM32F103C8T6",
                )
                result = run_task(
                    config=config,
                    project_root=tmpdir,
                    workspace_root=tmpdir,
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    task="修复 app_main.c 里的 printf 和注释问题",
                    project_name="DirectF1C",
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self.assertTrue(result["success"])
        self.assertEqual("fix", result["mode"])
        self.assertTrue(fix_mock.called)
        self.assertIn("自动修复", result["message"])

    def test_run_task_respects_empty_auto_fix_whitelist(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            config = AgentConfig()
            config.review.auto_fix_rule_ids = []
            project_path = str(Path(tmpdir) / "DirectF1C")
            issue_file = str(Path(project_path) / "App" / "Src" / "app_main.c")
            report = {
                "report": {
                    "passed": False,
                    "total_issues": 1,
                    "critical_count": 0,
                    "error_count": 1,
                    "warning_count": 0,
                    "issues": [
                        {
                            "severity": "error",
                            "rule_id": "EMB-004",
                            "file": issue_file,
                            "line": 10,
                            "message": "Driver code must not use printf.",
                        }
                    ],
                }
            }
            with patch("luxar.tools.run_task_dependencies.ProjectManager") as pm_cls, \
                 patch("luxar.tools.run_task_dependencies.runtime_adapters.run_review", return_value=report), \
                 patch("luxar.tools.run_task_support.RepairWorker.repair_file") as fix_mock:
                pm_cls.return_value.load_project.return_value = ProjectConfig(
                    name="DirectF1C",
                    path=project_path,
                    project_mode="firmware",
                    mcu="STM32F103C8T6",
                )
                result = run_task(
                    config=config,
                    project_root=tmpdir,
                    workspace_root=tmpdir,
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    task="修复 app_main.c 里的 printf 问题",
                    project_name="DirectF1C",
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self.assertTrue(result["success"])
        self.assertEqual("review", result["mode"])
        fix_mock.assert_not_called()

    def test_run_task_respects_auto_fix_disabled(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            config = AgentConfig()
            config.review.auto_fix_enabled = False
            project_path = str(Path(tmpdir) / "DirectF1C")
            issue_file = str(Path(project_path) / "App" / "Src" / "app_main.c")
            report = {
                "report": {
                    "passed": False,
                    "total_issues": 1,
                    "critical_count": 0,
                    "error_count": 1,
                    "warning_count": 0,
                    "issues": [
                        {
                            "severity": "error",
                            "rule_id": "EMB-004",
                            "file": issue_file,
                            "line": 10,
                            "message": "Driver code must not use printf.",
                        }
                    ],
                }
            }
            with patch("luxar.tools.run_task_dependencies.ProjectManager") as pm_cls, \
                 patch("luxar.tools.run_task_dependencies.runtime_adapters.run_review", return_value=report), \
                 patch("luxar.tools.run_task_support.RepairWorker.repair_file") as fix_mock:
                pm_cls.return_value.load_project.return_value = ProjectConfig(
                    name="DirectF1C",
                    path=project_path,
                    project_mode="firmware",
                    mcu="STM32F103C8T6",
                )
                result = run_task(
                    config=config,
                    project_root=tmpdir,
                    workspace_root=tmpdir,
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    task="修复 app_main.c 里的 printf 问题",
                    project_name="DirectF1C",
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self.assertTrue(result["success"])
        self.assertEqual("review", result["mode"])
        fix_mock.assert_not_called()

    def test_run_task_dispatches_through_runtime_worker_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AgentConfig()
            workflow = WorkflowRunResult(success=True, workflow="forge")
            with patch("luxar.tools.run_task_dependencies.ProjectManager") as pm_cls, \
                 patch("luxar.tools.run_task_dependencies.runtime_adapters.run_forge", return_value=workflow) as forge_mock:
                pm_cls.return_value.load_project.return_value = ProjectConfig(
                    name="DirectF1C",
                    path=str(Path(tmpdir) / "DirectF1C"),
                    project_mode="firmware",
                    mcu="STM32F103C8T6",
                )
                run_task(
                    config=config,
                    project_root=tmpdir,
                    workspace_root=tmpdir,
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    task="Generate project that blinks LED and prints UART.",
                    project_name="DirectF1C",
                )
        forge_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
