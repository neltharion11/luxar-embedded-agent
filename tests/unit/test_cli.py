from __future__ import annotations

import unittest
from unittest.mock import patch

from click.testing import CliRunner

from luxar.cli import main
from luxar.models.schemas import BuildResult, FlashResult, MonitorResult


class CliVNextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    @patch("luxar.cli.run_runtime")
    def test_run_routes_to_runtime(self, mock_runtime) -> None:
        mock_runtime.return_value = {"success": True, "mode": "runtime"}
        result = self.runner.invoke(
            main,
            ["run", "--project", "DirectF1C", "--task", "Bring up OLED and capture evidence"],
        )
        self.assertEqual(result.exit_code, 0)
        mock_runtime.assert_called_once_with(
            task="Bring up OLED and capture evidence",
            project="DirectF1C",
        )

    @patch("luxar.cli.explain_runtime_tool")
    def test_run_explain_returns_runtime_model(self, mock_explain) -> None:
        mock_explain.return_value = {"success": True, "mode": "explain"}
        result = self.runner.invoke(main, ["run", "--task", "ignored", "--explain"])
        self.assertEqual(result.exit_code, 0)
        mock_explain.assert_called_once_with()

    @patch("luxar.cli.skills_list")
    def test_skills_list(self, mock_skills_list) -> None:
        mock_skills_list.return_value = {"success": True, "skills": [{"name": "oled"}]}
        result = self.runner.invoke(main, ["skills", "list", "--category", "bringup"])
        self.assertEqual(result.exit_code, 0)
        mock_skills_list.assert_called_once_with(category="bringup")
        self.assertIn("oled", result.output)

    @patch("luxar.cli.skill_view")
    def test_skills_view(self, mock_skill_view) -> None:
        mock_skill_view.return_value = {"success": True, "skill": {"name": "oled-i2c-minimal"}}
        result = self.runner.invoke(main, ["skills", "view", "oled-i2c-minimal"])
        self.assertEqual(result.exit_code, 0)
        mock_skill_view.assert_called_once_with(name="oled-i2c-minimal")

    @patch("luxar.cli.skill_manage")
    def test_skills_manage(self, mock_skill_manage) -> None:
        mock_skill_manage.return_value = {"success": True}
        result = self.runner.invoke(
            main,
            [
                "skills",
                "manage",
                "--action",
                "create",
                "--name",
                "oled-bringup",
                "--category",
                "bringup",
            ],
        )
        self.assertEqual(result.exit_code, 0)
        mock_skill_manage.assert_called_once_with(
            action="create",
            name="oled-bringup",
            category="bringup",
            content="",
            old_string="",
            new_string="",
        )

    @patch("luxar.cli.skill_promote")
    def test_skills_promote(self, mock_skill_promote) -> None:
        mock_skill_promote.return_value = {"success": True, "promotion_level": "validated"}
        result = self.runner.invoke(main, ["skills", "promote", "oled-i2c-minimal"])
        self.assertEqual(result.exit_code, 0)
        mock_skill_promote.assert_called_once_with(
            name="oled-i2c-minimal",
            category="",
            promotion_level="validated",
        )

    @patch("luxar.cli.skill_execute")
    def test_skills_execute(self, mock_skill_execute) -> None:
        mock_skill_execute.return_value = {"success": True, "evidence": []}
        result = self.runner.invoke(
            main,
            [
                "skills",
                "execute",
                "oled-i2c-minimal",
                "--project",
                "DirectF1C",
                "--port",
                "COM3",
            ],
        )
        self.assertEqual(result.exit_code, 0)
        mock_skill_execute.assert_called_once_with(
            name="oled-i2c-minimal",
            category="",
            project="DirectF1C",
            port="COM3",
            baudrate=115200,
        )

    @patch("luxar.cli.memory_read")
    def test_memory_read(self, mock_memory_read) -> None:
        mock_memory_read.return_value = {"success": True, "content": "facts"}
        result = self.runner.invoke(main, ["memory", "read", "--target", "user"])
        self.assertEqual(result.exit_code, 0)
        mock_memory_read.assert_called_once_with(target="user")

    @patch("luxar.cli.memory_write")
    def test_memory_write(self, mock_memory_write) -> None:
        mock_memory_write.return_value = {"success": True}
        result = self.runner.invoke(
            main,
            ["memory", "write", "--content", "board uses i2c1", "--replace"],
        )
        self.assertEqual(result.exit_code, 0)
        mock_memory_write.assert_called_once_with(
            content="board uses i2c1",
            target="memory",
            append=False,
        )

    @patch("luxar.cli.memory_search")
    def test_memory_search(self, mock_memory_search) -> None:
        mock_memory_search.return_value = {"success": True, "results": []}
        result = self.runner.invoke(main, ["memory", "search", "--query", "oled"])
        self.assertEqual(result.exit_code, 0)
        mock_memory_search.assert_called_once_with(query="oled")

    @patch("luxar.cli.lessons_list")
    def test_memory_lessons(self, mock_lessons_list) -> None:
        mock_lessons_list.return_value = {"success": True, "lessons": []}
        result = self.runner.invoke(main, ["memory", "lessons"])
        self.assertEqual(result.exit_code, 0)
        mock_lessons_list.assert_called_once_with()

    @patch("luxar.cli.lesson_search")
    def test_memory_lesson_search(self, mock_lesson_search) -> None:
        mock_lesson_search.return_value = {"success": True, "lessons": []}
        result = self.runner.invoke(main, ["memory", "lesson-search", "--query", "screen dark"])
        self.assertEqual(result.exit_code, 0)
        mock_lesson_search.assert_called_once_with(query="screen dark", limit=5)

    @patch("luxar.cli.lesson_record")
    def test_memory_lesson_record(self, mock_lesson_record) -> None:
        mock_lesson_record.return_value = {"success": True, "lesson": {}}
        payload = '{"topic":"oled","symptom":"screen dark"}'
        result = self.runner.invoke(main, ["memory", "lesson-record", "--payload", payload])
        self.assertEqual(result.exit_code, 0)
        mock_lesson_record.assert_called_once_with(
            payload={"topic": "oled", "symptom": "screen dark"},
            promoted=False,
        )

    @patch("luxar.cli.lesson_promote")
    def test_memory_lesson_promote(self, mock_lesson_promote) -> None:
        mock_lesson_promote.return_value = {"success": True}
        result = self.runner.invoke(
            main,
            ["memory", "lesson-promote", "--slug", "oled-dark-screen", "--evidence-count", "2"],
        )
        self.assertEqual(result.exit_code, 0)
        mock_lesson_promote.assert_called_once_with(slug="oled-dark-screen", evidence_count=2)

    @patch("luxar.cli.workspace_inspect")
    def test_workspace_inspect(self, mock_workspace_inspect) -> None:
        mock_workspace_inspect.return_value = {"success": True, "projects": []}
        result = self.runner.invoke(main, ["workspace", "inspect"])
        self.assertEqual(result.exit_code, 0)
        mock_workspace_inspect.assert_called_once_with()

    @patch("luxar.cli.workspace_build")
    def test_workspace_build(self, mock_workspace_build) -> None:
        mock_workspace_build.return_value = BuildResult(success=True)
        result = self.runner.invoke(main, ["workspace", "build", "--project", "DirectF1C"])
        self.assertEqual(result.exit_code, 0)
        mock_workspace_build.assert_called_once_with(project="DirectF1C", clean=False)

    @patch("luxar.cli.workspace_flash")
    def test_workspace_flash(self, mock_workspace_flash) -> None:
        mock_workspace_flash.return_value = FlashResult(success=True)
        result = self.runner.invoke(main, ["workspace", "flash", "--project", "DirectF1C"])
        self.assertEqual(result.exit_code, 0)
        mock_workspace_flash.assert_called_once_with(project="DirectF1C", probe="")

    @patch("luxar.cli.workspace_monitor")
    def test_workspace_monitor(self, mock_workspace_monitor) -> None:
        mock_workspace_monitor.return_value = MonitorResult(success=True)
        result = self.runner.invoke(
            main,
            ["workspace", "monitor", "--project", "DirectF1C", "--port", "COM3"],
        )
        self.assertEqual(result.exit_code, 0)
        mock_workspace_monitor.assert_called_once_with(
            project="DirectF1C",
            port="COM3",
            baudrate=115200,
        )

    @patch("luxar.cli.workspace_probe")
    def test_workspace_probe(self, mock_workspace_probe) -> None:
        mock_workspace_probe.return_value = {"success": True, "probe_type": "i2c"}
        result = self.runner.invoke(main, ["workspace", "probe", "--project", "DirectF1C"])
        self.assertEqual(result.exit_code, 0)
        mock_workspace_probe.assert_called_once_with(project="DirectF1C", probe_type="i2c")

    def test_legacy_commands_are_removed(self) -> None:
        for command in ("forge", "generate-driver", "fix-code", "review", "workflow", "debug-loop"):
            result = self.runner.invoke(main, [command])
            self.assertNotEqual(result.exit_code, 0, msg=command)
            self.assertIn("No such command", result.output)


if __name__ == "__main__":
    unittest.main()
