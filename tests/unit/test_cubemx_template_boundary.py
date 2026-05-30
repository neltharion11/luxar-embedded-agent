from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from luxar.core.project_manager import ProjectManager
from luxar.tools.skills_tool import skill_execute
from luxar.tools.workspace_tool import workspace_create_project


class CubeMXTemplateBoundaryTests(unittest.TestCase):
    def test_cubemx_template_contains_only_app_and_bsp_roots(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        template = repo_root / "workspace" / "templates" / "cubemx"

        self.assertEqual(["App", "BSP"], sorted(path.name for path in template.iterdir()))
        self.assertTrue((template / "App").is_dir())
        self.assertTrue((template / "BSP").is_dir())
        self.assertFalse((template / "CMakeLists.txt").exists())
        self.assertFalse((template / "CMakePresets.json").exists())
        self.assertFalse((template / "cmake").exists())
        self.assertFalse((template / "Core").exists())
        self.assertFalse((template / "Drivers").exists())
        self.assertFalse(list(template.rglob("*.c")))
        self.assertFalse(list(template.rglob("*.h")))

    def test_workspace_create_cubemx_project_leaves_only_user_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "projects"
            project = ProjectManager(str(workspace)).create_project(
                name="CubeOnly",
                mcu="STM32F103C8T6",
                platform="stm32cubemx",
                runtime="freertos",
                project_mode="cubemx",
            )
            project_dir = Path(project.path)

            self.assertTrue((project_dir / "App").is_dir())
            self.assertTrue((project_dir / "BSP").is_dir())
            self.assertFalse((project_dir / "Core").exists())
            self.assertFalse((project_dir / "Drivers").exists())
            self.assertFalse((project_dir / "CMakeLists.txt").exists())
            self.assertFalse((project_dir / "CMakePresets.json").exists())
            self.assertFalse((project_dir / ".gitignore").exists())

    def test_init_project_framework_cubemx_copies_only_app_and_bsp(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "projects"
            project_dir = workspace / "CubeSkill"
            project_dir.mkdir(parents=True)
            (project_dir / ".agent_project.json").write_text(
                json.dumps(
                    {
                        "name": "CubeSkill",
                        "mcu": "STM32F103C8T6",
                        "platform": "stm32cubemx",
                        "runtime": "baremetal",
                        "project_mode": "cubemx",
                    }
                ),
                encoding="utf-8",
            )
            cm = Mock()
            cm.workspace_root.return_value = workspace
            cm.project_root.return_value = repo_root
            manager = Mock()
            manager.view.return_value = {"metadata": {}, "content": ""}

            with patch("luxar.tools.skills_tool._manager", return_value=manager), patch(
                "luxar.core.config_manager.ConfigManager", return_value=cm
            ):
                result = skill_execute("init_project_framework", category="project", project="CubeSkill")

            self.assertTrue(result["success"])
            self.assertTrue((project_dir / "App").is_dir())
            self.assertTrue((project_dir / "BSP").is_dir())
            self.assertFalse((project_dir / "Core").exists())
            self.assertFalse((project_dir / "Drivers").exists())
            self.assertFalse((project_dir / "cmake").exists())
            self.assertFalse((project_dir / "CMakeLists.txt").exists())


if __name__ == "__main__":
    unittest.main()
