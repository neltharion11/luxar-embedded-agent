from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from luxar.api import create_app
from luxar.core.config_manager import AgentConfig
from luxar.core.project_manager import ProjectManager
from luxar.server.chat_support import inject_project_metadata
from luxar.tools import workspace_tool


class NonFreeRtosPrereleaseTests(unittest.TestCase):
    def test_config_endpoint_redacts_api_keys(self) -> None:
        cfg = AgentConfig()
        cfg.api_keys = {"deepseek": "secret-value", "openai": ""}
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = cfg
                cm.project_root.return_value = Path(tmpdir)
                cm.workspace_root.return_value = Path(tmpdir) / "workspace" / "projects"
                cm.driver_library_root.return_value = Path(tmpdir) / "workspace" / "driver_library"
                cm.legacy_skill_library_root.return_value = Path(tmpdir) / "workspace" / "skill_library"
                cm.skills_root.return_value = Path("workspace") / "skills"
                cm.lesson_library_root.return_value = Path(tmpdir) / "workspace" / "lessons"
                cm.memory_root.return_value = Path(tmpdir) / "workspace" / "memory"
                cm.prompts_root.return_value = Path(tmpdir) / "workspace" / "prompts"

                with TestClient(create_app()) as client:
                    response = client.get("/api/config")

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("***", payload["api_keys"]["deepseek"])
        self.assertEqual("", payload["api_keys"]["openai"])
        self.assertNotIn("secret-value", response.text)

    def test_baremetal_prompt_context_does_not_inject_freertos_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace" / "projects"
            project_dir = workspace / "BareDemo"
            project_dir.mkdir(parents=True)
            (project_dir / ".agent_project.json").write_text(
                json.dumps(
                    {
                        "name": "BareDemo",
                        "mcu": "STM32F103C8T6",
                        "platform": "stm32cubemx",
                        "runtime": "baremetal",
                        "project_mode": "cubemx",
                    }
                ),
                encoding="utf-8",
            )
            cm = type("CM", (), {"workspace_root": lambda self: workspace})()

            prompt = inject_project_metadata("base", "BareDemo", cm)

        self.assertIn("CubeMX Project Development Rules", prompt)
        self.assertNotIn("FreeRTOS System Rules", prompt)
        self.assertNotIn("osThreadNew", prompt)
        self.assertNotIn("semaphore", prompt.lower())

    def test_project_matrix_defers_freertos_and_covers_baremetal_platforms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager(str(Path(tmpdir) / "projects"))
            cubemx = manager.create_project(
                name="CubeBare",
                mcu="STM32F103C8T6",
                platform="stm32cubemx",
                runtime="baremetal",
                project_mode="cubemx",
            )
            firmware = manager.create_project(
                name="FirmwareBare",
                mcu="STM32F103C8T6",
                platform="stm32firmware",
                runtime="baremetal",
                project_mode="firmware",
            )

        self.assertEqual(("stm32cubemx", "baremetal"), (cubemx.platform, cubemx.runtime))
        self.assertEqual(("stm32firmware", "baremetal"), (firmware.platform, firmware.runtime))

    def test_workspace_file_tools_reject_traversal_and_mutating_shell_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "projects"
            project_dir = workspace / "Demo"
            project_dir.mkdir(parents=True)
            cm = type("CM", (), {"workspace_root": lambda self: workspace})()
            previous = workspace_tool._cm_instance
            workspace_tool._cm_instance = cm
            try:
                write_result = workspace_tool.workspace_write_file("Demo", "../escape.txt", "bad")
                shell_result = workspace_tool.workspace_shell("Demo", "del file.txt")
            finally:
                workspace_tool._cm_instance = previous

        self.assertFalse(write_result["success"])
        self.assertIn("Access denied", write_result["error"])
        self.assertFalse(shell_result["success"])
        self.assertIn("not allowed", shell_result["error"])

    def test_freertos_library_presence_is_environment_fact_only(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        freertos_root = (
            repo_root
            / "workspace"
            / "firmware_library"
            / "stm32"
            / "STM32Cube_FW_F1_V1.8.7"
            / "Middlewares"
            / "Third_Party"
            / "FreeRTOS"
            / "Source"
        )
        self.assertTrue((freertos_root / "include" / "FreeRTOS.h").exists())
        self.assertTrue((freertos_root / "tasks.c").exists())


if __name__ == "__main__":
    unittest.main()
