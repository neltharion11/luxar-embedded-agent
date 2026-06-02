from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from luxar.tools.skills_tool import skill_execute


class STM32FirmwareVSCodeTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]

    def test_firmware_templates_include_vscode_debug_and_flash_configs(self) -> None:
        for template_name in ("baremetal", "freertos"):
            with self.subTest(template=template_name):
                template = self.repo_root / "workspace" / "templates" / template_name
                launch_path = template / ".vscode" / "launch.json"
                tasks_path = template / ".vscode" / "tasks.json"
                settings_path = template / ".vscode" / "settings.json"

                self.assertTrue(launch_path.exists())
                self.assertTrue(tasks_path.exists())
                self.assertTrue(settings_path.exists())

                launch = json.loads(launch_path.read_text(encoding="utf-8"))
                config = launch["configurations"][0]
                self.assertEqual("cortex-debug", config["type"])
                self.assertEqual("stlink", config["servertype"])
                self.assertEqual("LUXAR: build", config["preLaunchTask"])
                self.assertEqual("main", config["runToEntryPoint"])
                self.assertIn("build/Debug/{PROJECT_NAME}.elf", config["executable"])
                self.assertIn("arm-none-eabi-gdb.exe", config["gdbPath"])

                tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
                labels = {task["label"] for task in tasks["tasks"]}
                self.assertIn("LUXAR: configure", labels)
                self.assertIn("LUXAR: build", labels)
                self.assertIn("LUXAR: flash", labels)
                self.assertIn("LUXAR: build+flash", labels)
                tasks_text = tasks_path.read_text(encoding="utf-8")
                self.assertIn("cmake.exe", tasks_text)
                self.assertIn("STM32_Programmer_CLI.exe", tasks_text)

                settings = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertEqual("Debug", settings["cmake.configurePreset"])
                self.assertEqual("Debug", settings["cmake.buildPreset"])
                self.assertIn("cmake.exe", settings["cmake.cmakePath"])

    def test_template_tools_referenced_by_vscode_configs_exist(self) -> None:
        self.assertTrue(
            (
                self.repo_root
                / "workspace"
                / "toolchains"
                / "gcc-arm"
                / "bin"
                / "arm-none-eabi-gdb.exe"
            ).exists()
        )
        self.assertTrue(
            (
                self.repo_root
                / "workspace"
                / "toolchains"
                / "programmer"
                / "bin"
                / "STM32_Programmer_CLI.exe"
            ).exists()
        )
        self.assertTrue((self.repo_root / "workspace" / "toolchains" / "ninja" / "ninja.exe").exists())
        self.assertTrue((self.repo_root / "workspace" / "toolchains" / "cmake" / "bin" / "cmake.exe").exists())

    def test_init_project_framework_replaces_vscode_project_placeholders(self) -> None:
        cases = [
            ("BareF5", "baremetal"),
            ("FreeF5", "freertos"),
        ]
        for project_name, runtime in cases:
            with self.subTest(runtime=runtime):
                with tempfile.TemporaryDirectory() as tmpdir:
                    workspace = Path(tmpdir) / "projects"
                    project_dir = workspace / project_name
                    project_dir.mkdir(parents=True)
                    (project_dir / ".agent_project.json").write_text(
                        json.dumps(
                            {
                                "name": project_name,
                                "mcu": "STM32F103C8",
                                "platform": "stm32firmware",
                                "runtime": runtime,
                                "project_mode": "firmware",
                            }
                        ),
                        encoding="utf-8",
                    )

                    cm = Mock()
                    cm.workspace_root.return_value = workspace
                    cm.project_root.return_value = self.repo_root
                    cm.firmware_library_root.return_value = self.repo_root / "workspace" / "firmware_library"
                    manager = Mock()
                    manager.view.return_value = {"metadata": {}, "content": ""}

                    with patch("luxar.tools.skills_tool._manager", return_value=manager), patch(
                        "luxar.core.config_manager.ConfigManager", return_value=cm
                    ):
                        result = skill_execute("init_project_framework", category="project", project=project_name)

                    self.assertTrue(result["success"])
                    launch_text = (project_dir / ".vscode" / "launch.json").read_text(encoding="utf-8")
                    tasks_text = (project_dir / ".vscode" / "tasks.json").read_text(encoding="utf-8")
                    self.assertNotIn("{PROJECT_NAME}", launch_text)
                    self.assertNotIn("{PROJECT_NAME}", tasks_text)
                    self.assertIn(f"build/Debug/{project_name}.elf", launch_text)
                    self.assertIn('"device": "STM32F103C8"', launch_text)
                    self.assertIn(f"build/Debug/{project_name}.hex", tasks_text)
                    self.assertTrue((project_dir / "FIRMWARE_PACKAGE.txt").exists())
                    self.assertEqual("F1", (project_dir / "STM32_FAMILY.txt").read_text(encoding="utf-8").strip())

    def test_baremetal_cmake_uses_stable_target_and_correct_cmsis_path(self) -> None:
        cmake = (self.repo_root / "workspace" / "templates" / "baremetal" / "CMakeLists.txt").read_text(
            encoding="utf-8"
        )

        self.assertIn("add_executable(${CMAKE_PROJECT_NAME})", cmake)
        self.assertNotIn("add_executable(${CMAKE_PROJECT_NAME}.elf)", cmake)
        self.assertIn('set(CMSIS_CORE "${FW_LIB}/Drivers/CMSIS/Include")', cmake)
        self.assertIn("LINKER_SCRIPT", cmake)
        self.assertIn("{STM32_DEVICE_DEFINE}", cmake)
        self.assertNotIn("STM32F103xB", cmake)


if __name__ == "__main__":
    unittest.main()
