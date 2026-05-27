from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner
from fastapi.testclient import TestClient

from luxar.agent.runtime import explain_runtime, run_runtime_task
from luxar.api import create_app
from luxar.cli import main
from luxar.core.config_manager import AgentConfig
from luxar.memory.lesson_store import LessonStore
from luxar.skills.manager import SkillManagerVNext


class VNextRuntimeTests(unittest.TestCase):
    def test_runtime_explain_reports_skill_first_model(self) -> None:
        payload = explain_runtime()
        self.assertEqual("skill-first", payload["runtime_model"])
        self.assertIn("skills", payload["core_primitives"])

    def test_skill_manager_lists_seeded_skills(self) -> None:
        manager = SkillManagerVNext(Path("workspace") / "skills")
        skills = manager.list_skills()
        self.assertTrue(any(item["name"] == "oled-ch1116" for item in skills))

    def test_skill_manager_lists_seeded_executable_skill(self) -> None:
        manager = SkillManagerVNext(Path("workspace") / "skills")
        skills = manager.executable_skills(category="bringup")
        self.assertTrue(any(item["name"] == "oled-i2c-minimal" for item in skills))

    def test_lesson_store_record_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = LessonStore(Path(tmpdir))
            store.record(
                {
                    "slug": "oled-dark-screen",
                    "topic": "oled_dark_screen",
                    "symptom": "screen stays dark",
                    "hypothesis": "bring-up not validated",
                    "evidence": "UART output showed no I2C ACK",
                    "resolution": "run minimal executable skill first",
                    "outcome": "Screen initialized correctly after skill execution",
                }
            )
            matches = store.search("dark")
        self.assertEqual(1, len(matches))
        self.assertEqual("oled_dark_screen", matches[0]["topic"])

    def test_runtime_run_matches_seeded_skill_and_executable_skill(self) -> None:
        with patch("luxar.agent.runtime.ConfigManager") as cm_cls:
            cm = cm_cls.return_value
            cfg = AgentConfig()
            cm.ensure_default_config.return_value = cfg
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                (root / "workspace" / "projects").mkdir(parents=True, exist_ok=True)
                (root / "workspace" / "driver_library").mkdir(parents=True, exist_ok=True)
                (root / "workspace" / "skill_library").mkdir(parents=True, exist_ok=True)
                cm.project_root.return_value = root
                cm.workspace_root.return_value = root / "workspace" / "projects"
                cm.driver_library_root.return_value = root / "workspace" / "driver_library"
                cm.skill_library_root.return_value = root / "workspace" / "skills"
                cm.legacy_skill_library_root.return_value = root / "workspace" / "skill_library"
                cm.skills_root.return_value = Path("workspace") / "skills"
                cm.lesson_library_root.return_value = root / "workspace" / "lessons"
                cm.memory_root.return_value = root / "workspace" / "memory"
                cm.prompts_root.return_value = root / "workspace" / "prompts"
                result = run_runtime_task("Bring up a CH1116 OLED over I2C", project="demo")
        self.assertTrue(result["success"])
        self.assertTrue(any(item["name"] == "oled-ch1116" for item in result["selected_skills"]))
        self.assertTrue(any(item["name"] == "oled-i2c-minimal" for item in result["selected_executable_skills"]))


class VNextCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_runtime_explain_command(self) -> None:
        result = self.runner.invoke(main, ["run", "--task", "explain runtime", "--explain"])
        self.assertEqual(0, result.exit_code)
        self.assertIn("skill-first", result.output)

    def test_skills_list_command(self) -> None:
        result = self.runner.invoke(main, ["skills", "list"])
        self.assertEqual(0, result.exit_code)
        self.assertIn("oled-ch1116", result.output)

    def test_skills_execute_command(self) -> None:
        result = self.runner.invoke(main, ["skills", "execute", "oled-i2c-minimal"])
        self.assertEqual(0, result.exit_code)
        self.assertIn("executable", result.output)


class VNextApiTests(unittest.TestCase):
    def test_runtime_explain_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = AgentConfig()
                cm.project_root.return_value = Path(tmpdir)
                cm.workspace_root.return_value = Path(tmpdir) / "workspace" / "projects"
                cm.driver_library_root.return_value = Path(tmpdir) / "workspace" / "driver_library"
                cm.skill_library_root.return_value = Path(tmpdir) / "workspace" / "skills"
                cm.legacy_skill_library_root.return_value = Path(tmpdir) / "workspace" / "skill_library"
                cm.skills_root.return_value = Path("workspace") / "skills"
                cm.lesson_library_root.return_value = Path(tmpdir) / "workspace" / "lessons"
                cm.memory_root.return_value = Path(tmpdir) / "workspace" / "memory"
                cm.prompts_root.return_value = Path(tmpdir) / "workspace" / "prompts"
                with TestClient(create_app()) as client:
                    response = client.get("/api/runtime/explain")
        self.assertEqual(200, response.status_code)
        self.assertEqual("skill-first", response.json()["runtime_model"])

    def test_runtime_skills_endpoint_lists_vnext_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("luxar.tools.skills_tool.ConfigManager") as skills_cm_cls:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = AgentConfig()
                cm.project_root.return_value = Path(tmpdir)
                cm.workspace_root.return_value = Path(tmpdir) / "workspace" / "projects"
                cm.driver_library_root.return_value = Path(tmpdir) / "workspace" / "driver_library"
                cm.skill_library_root.return_value = Path(tmpdir) / "workspace" / "skills"
                cm.legacy_skill_library_root.return_value = Path(tmpdir) / "workspace" / "skill_library"
                cm.skills_root.return_value = Path("workspace") / "skills"
                cm.lesson_library_root.return_value = Path(tmpdir) / "workspace" / "lessons"
                cm.memory_root.return_value = Path(tmpdir) / "workspace" / "memory"
                cm.prompts_root.return_value = Path(tmpdir) / "workspace" / "prompts"
                skills_cm_cls.return_value = cm
                with TestClient(create_app()) as client:
                    response = client.get("/api/skills")
        self.assertEqual(200, response.status_code)
        self.assertTrue(any(item["name"] == "oled-ch1116" for item in response.json()["skills"]))

    def test_memory_lessons_endpoint_lists_vnext_lessons(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("luxar.tools.memory_tool.ConfigManager") as memory_cm_cls:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = AgentConfig()
                cm.project_root.return_value = Path(tmpdir)
                cm.workspace_root.return_value = Path(tmpdir) / "workspace" / "projects"
                cm.driver_library_root.return_value = Path(tmpdir) / "workspace" / "driver_library"
                cm.skill_library_root.return_value = Path(tmpdir) / "workspace" / "skills"
                cm.legacy_skill_library_root.return_value = Path(tmpdir) / "workspace" / "skill_library"
                cm.skills_root.return_value = Path("workspace") / "skills"
                cm.lesson_library_root.return_value = Path(tmpdir) / "workspace" / "lessons"
                cm.memory_root.return_value = Path(tmpdir) / "workspace" / "memory"
                cm.prompts_root.return_value = Path(tmpdir) / "workspace" / "prompts"
                memory_cm_cls.return_value = cm
                LessonStore(cm.lesson_library_root.return_value).record(
                    {
                        "slug": "oled-dark-screen",
                        "topic": "oled_dark_screen",
                        "symptom": "screen stays dark",
                        "hypothesis": "bringup incomplete",
                        "evidence": "No I2C ACK seen on logic analyser",
                        "resolution": "run executable skill first",
                        "outcome": "OLED came up after following executable skill steps",
                    }
                )
                with TestClient(create_app()) as client:
                    response = client.get("/api/memory/lessons")
        self.assertEqual(200, response.status_code)
        self.assertTrue(any(item["topic"] == "oled_dark_screen" for item in response.json()["lessons"]))
