from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luxar.core.config_manager import AgentConfig
from luxar.core.task_router import TaskRouter
from luxar.models.schemas import BuildResult, ProjectConfig, WorkflowRunResult
from luxar.tools.run_task import run_task


class TaskRouterTests(unittest.TestCase):
    def test_explain_request_routes_to_explain(self) -> None:
        plan = TaskRouter().route(task="Explain the BMI270 SPI frame format.", docs=["docs/bmi270.pdf"])
        self.assertEqual("explain", plan.intent.intent_type)

    def test_generate_project_routes_to_forge(self) -> None:
        plan = TaskRouter().route(task="Generate a project that blinks LED and prints UART logs.", project="DirectF1C")
        self.assertEqual("forge_project", plan.intent.intent_type)

    def test_fix_compile_error_routes_to_debug_or_fix(self) -> None:
        plan = TaskRouter().route(task="Fix the compile error and rebuild the project.", project="DirectF1C")
        self.assertEqual("debug_project", plan.intent.intent_type)

    def test_docs_push_task_toward_analysis(self) -> None:
        plan = TaskRouter().route(task="Help me wire this device.", docs=["docs/bmi270.pdf"])
        self.assertIn(plan.intent.intent_type, {"forge_project", "explain"})
        self.assertTrue(plan.steps[0] in {"parse_docs", "analyze_docs"})


class RunTaskTests(unittest.TestCase):
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
            with patch("luxar.tools.run_task.ProjectManager") as pm_cls, \
                 patch("luxar.tools.run_task.run_forge_project", return_value=workflow):
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

    def test_run_task_plan_only_returns_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = AgentConfig()
            workflow = WorkflowRunResult(success=True, workflow="forge")
            with patch("luxar.tools.run_task.ProjectManager") as pm_cls, \
                 patch("luxar.tools.run_task.run_forge_project", return_value=workflow):
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


if __name__ == "__main__":
    unittest.main()
