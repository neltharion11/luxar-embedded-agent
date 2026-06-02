from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from luxar.tools.skills_tool import skill_execute


class FreeRtosFirmwareTemplateTests(unittest.TestCase):
    def test_freertos_template_is_firmware_not_cubemx_project(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        template = repo_root / "workspace" / "templates" / "freertos"

        self.assertTrue((template / "Core" / "Inc" / "FreeRTOSConfig.h").exists())
        self.assertTrue((template / "Core" / "Src" / "freertos.c").exists())
        self.assertTrue((template / "App" / "Src" / "app_main.c").exists())
        self.assertFalse(list(template.glob("*.ioc")))
        self.assertFalse((template / ".mxproject").exists())

    def test_freertos_template_reuses_luxar_firmware_library(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        cmake = (repo_root / "workspace" / "templates" / "freertos" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )

        self.assertIn("{STM32_FIRMWARE_PACKAGE}", cmake)
        self.assertIn("FREERTOS_ROOT", cmake)
        self.assertIn("${FREERTOS_ROOT}/CMSIS_RTOS_V2/cmsis_os2.c", cmake)
        self.assertIn("${FREERTOS_ROOT}/portable/GCC/{STM32_FREERTOS_PORT}/port.c", cmake)
        self.assertNotIn("${CMAKE_CURRENT_SOURCE_DIR}/../../Middlewares", cmake)
        self.assertIn("App/Src/app_main.c", cmake)

    def test_init_project_framework_selects_freertos_template_for_firmware_runtime(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "projects"
            project_dir = workspace / "FreeDemo"
            project_dir.mkdir(parents=True)
            (project_dir / ".agent_project.json").write_text(
                json.dumps(
                    {
                        "name": "FreeDemo",
                        "mcu": "STM32F103C8",
                        "platform": "stm32firmware",
                        "runtime": "freertos",
                        "project_mode": "firmware",
                    }
                ),
                encoding="utf-8",
            )

            cm = Mock()
            cm.workspace_root.return_value = workspace
            cm.project_root.return_value = repo_root
            cm.firmware_library_root.return_value = repo_root / "workspace" / "firmware_library"
            manager = Mock()
            manager.view.return_value = {"metadata": {}, "content": ""}

            with patch("luxar.tools.skills_tool._manager", return_value=manager), patch(
                "luxar.core.config_manager.ConfigManager", return_value=cm
            ):
                result = skill_execute("init_project_framework", category="project", project="FreeDemo")

            self.assertTrue(result["success"])
            self.assertTrue((project_dir / "Core" / "Src" / "freertos.c").exists())
            self.assertTrue((project_dir / "Core" / "Inc" / "FreeRTOSConfig.h").exists())
            self.assertTrue((project_dir / "startup_stm32f103xb.s").exists())
            self.assertTrue((project_dir / "STM32F103XB_FLASH.ld").exists())
            self.assertTrue((project_dir / "FIRMWARE_PACKAGE.txt").exists())
            self.assertEqual("F1", (project_dir / "STM32_FAMILY.txt").read_text(encoding="utf-8").strip())
            self.assertTrue((project_dir / "App" / "Src" / "app_main.c").exists())
            self.assertFalse((project_dir / "Core" / "Src" / "app_main.c").exists())
            self.assertEqual(
                "freertos",
                json.loads((project_dir / ".agent_project.json").read_text(encoding="utf-8"))["runtime"],
            )


if __name__ == "__main__":
    unittest.main()
