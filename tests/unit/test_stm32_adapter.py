from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.config_manager import AgentConfig
from luxar.core.toolchain_manager import ToolchainManager
from luxar.platforms.stm32_adapter import STM32CubeMXAdapter


class STM32AdapterBuildTests(unittest.TestCase):
    def test_build_injects_compilers_when_project_toolchain_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "Demo"
            project.mkdir(parents=True, exist_ok=True)
            (project / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.22)\nproject(Demo C ASM)\n",
                encoding="utf-8",
            )

            tool_bin = root / "workspace" / "toolchains" / "gcc-arm" / "bin"
            tool_bin.mkdir(parents=True, exist_ok=True)
            for name in ("arm-none-eabi-gcc.exe", "arm-none-eabi-g++.exe"):
                (tool_bin / name).write_text("", encoding="utf-8")
            ninja_bin = root / "workspace" / "toolchains" / "ninja"
            ninja_bin.mkdir(parents=True, exist_ok=True)
            (ninja_bin / "ninja.exe").write_text("", encoding="utf-8")
            cmake_bin = root / "workspace" / "toolchains" / "cmake" / "bin"
            cmake_bin.mkdir(parents=True, exist_ok=True)
            (cmake_bin / "cmake.exe").write_text("", encoding="utf-8")

            config = AgentConfig()
            manager = ToolchainManager(config=config, project_root=root)
            adapter = STM32CubeMXAdapter(toolchain_manager=manager)

            configure = mock.Mock(returncode=0, stdout="", stderr="")
            build = mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch("luxar.platforms.stm32_adapter.subprocess.run", side_effect=[configure, build]) as run_mock:
                result = adapter.build(str(project))

            self.assertTrue(result.success)
            configure_cmd = run_mock.call_args_list[0].args[0]
            self.assertTrue(any(arg.startswith("-DCMAKE_TOOLCHAIN_FILE=") for arg in configure_cmd))
            self.assertTrue(any(arg.startswith("-DCMAKE_C_COMPILER=") for arg in configure_cmd))
            self.assertTrue(any(arg.startswith("-DCMAKE_ASM_COMPILER=") for arg in configure_cmd))
            toolchain_arg = next(arg for arg in configure_cmd if arg.startswith("-DCMAKE_TOOLCHAIN_FILE="))
            toolchain_path = Path(toolchain_arg.split("=", 1)[1])
            self.assertTrue(toolchain_path.exists())
            toolchain_text = toolchain_path.read_text(encoding="utf-8")
            self.assertIn("arm-none-eabi-gcc", toolchain_text)
            self.assertNotIn("LUXAR_TARGET_FLAGS", toolchain_text)

    def test_build_derives_cortex_m3_flags_from_agent_project_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "Demo"
            project.mkdir(parents=True, exist_ok=True)
            (project / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.22)\nproject(Demo C ASM)\n",
                encoding="utf-8",
            )
            (project / ".agent_project.json").write_text(
                '{"mcu":"STM32F103C8T6"}',
                encoding="utf-8",
            )

            tool_bin = root / "workspace" / "toolchains" / "gcc-arm" / "bin"
            tool_bin.mkdir(parents=True, exist_ok=True)
            for name in ("arm-none-eabi-gcc.exe", "arm-none-eabi-g++.exe"):
                (tool_bin / name).write_text("", encoding="utf-8")
            ninja_bin = root / "workspace" / "toolchains" / "ninja"
            ninja_bin.mkdir(parents=True, exist_ok=True)
            (ninja_bin / "ninja.exe").write_text("", encoding="utf-8")
            cmake_bin = root / "workspace" / "toolchains" / "cmake" / "bin"
            cmake_bin.mkdir(parents=True, exist_ok=True)
            (cmake_bin / "cmake.exe").write_text("", encoding="utf-8")

            config = AgentConfig()
            manager = ToolchainManager(config=config, project_root=root)
            adapter = STM32CubeMXAdapter(toolchain_manager=manager)

            configure = mock.Mock(returncode=0, stdout="", stderr="")
            build = mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch("luxar.platforms.stm32_adapter.subprocess.run", side_effect=[configure, build]) as run_mock:
                result = adapter.build(str(project))

            self.assertTrue(result.success)
            toolchain_arg = next(
                arg for arg in run_mock.call_args_list[0].args[0]
                if arg.startswith("-DCMAKE_TOOLCHAIN_FILE=")
            )
            toolchain_path = Path(toolchain_arg.split("=", 1)[1])
            toolchain_text = toolchain_path.read_text(encoding="utf-8")
            self.assertIn('set(LUXAR_TARGET_FLAGS "-mcpu=cortex-m3 -mthumb")', toolchain_text)

    def test_build_resets_cached_cmake_state_when_project_toolchain_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "Demo"
            build_dir = project / "build"
            cmake_dir = project / "cmake"
            project.mkdir(parents=True, exist_ok=True)
            build_dir.mkdir(parents=True, exist_ok=True)
            cmake_dir.mkdir(parents=True, exist_ok=True)
            (project / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.22)\nproject(Demo C ASM)\n",
                encoding="utf-8",
            )
            (cmake_dir / "toolchain-arm-none-eabi.cmake").write_text(
                "set(CMAKE_SYSTEM_NAME Generic)\n",
                encoding="utf-8",
            )
            (build_dir / "CMakeCache.txt").write_text("stale cache", encoding="utf-8")
            (build_dir / "CMakeFiles").mkdir(parents=True, exist_ok=True)
            (build_dir / "CMakeFiles" / "stale.txt").write_text("old", encoding="utf-8")

            tool_bin = root / "workspace" / "toolchains" / "cmake" / "bin"
            tool_bin.mkdir(parents=True, exist_ok=True)
            (tool_bin / "cmake.exe").write_text("", encoding="utf-8")

            config = AgentConfig()
            manager = ToolchainManager(config=config, project_root=root)
            adapter = STM32CubeMXAdapter(toolchain_manager=manager)

            configure = mock.Mock(returncode=0, stdout="", stderr="")
            build = mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch("luxar.platforms.stm32_adapter.subprocess.run", side_effect=[configure, build]):
                result = adapter.build(str(project))

            self.assertTrue(result.success)
            self.assertFalse((build_dir / "CMakeCache.txt").exists())
            self.assertFalse((build_dir / "CMakeFiles").exists())

    def test_build_extracts_compiler_errors_from_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "Demo"
            project.mkdir(parents=True, exist_ok=True)
            (project / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.22)\nproject(Demo C ASM)\n",
                encoding="utf-8",
            )

            cmake_bin = root / "workspace" / "toolchains" / "cmake" / "bin"
            cmake_bin.mkdir(parents=True, exist_ok=True)
            (cmake_bin / "cmake.exe").write_text("", encoding="utf-8")

            config = AgentConfig()
            manager = ToolchainManager(config=config, project_root=root)
            adapter = STM32CubeMXAdapter(toolchain_manager=manager)

            configure = mock.Mock(returncode=0, stdout="", stderr="")
            build = mock.Mock(returncode=1, stdout="main.c:10:5: error: unknown type name UART_HandleTypeDef\n", stderr="")
            with mock.patch("luxar.platforms.stm32_adapter.subprocess.run", side_effect=[configure, build]):
                result = adapter.build(str(project))

            self.assertFalse(result.success)
            self.assertTrue(any("UART_HandleTypeDef" in item for item in result.errors))


class STM32AdapterFlashTests(unittest.TestCase):
    def test_flash_augments_probe_missing_when_stlink_is_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "Demo"
            build_dir = project / "build"
            build_dir.mkdir(parents=True, exist_ok=True)
            artifact = build_dir / "demo.elf"
            artifact.write_text("", encoding="utf-8")

            programmer_bin = root / "workspace" / "toolchains" / "programmer" / "bin"
            programmer_bin.mkdir(parents=True, exist_ok=True)
            (programmer_bin / "STM32_Programmer_CLI.exe").write_text("", encoding="utf-8")

            config = AgentConfig()
            manager = ToolchainManager(config=config, project_root=root)
            adapter = STM32CubeMXAdapter(toolchain_manager=manager)

            list_result = mock.Mock(returncode=0, stdout="ST-LINK SN : 12345678", stderr="")
            flash_result = mock.Mock(returncode=1, stdout="", stderr="No debug probe detected")
            with mock.patch("luxar.platforms.stm32_adapter.subprocess.run", side_effect=[list_result, flash_result]):
                result = adapter.flash(str(project))

            self.assertFalse(result.success)
            self.assertIn("Host-side ST-Link enumeration succeeded", result.stderr)
            self.assertIn("Probe inventory", result.stderr)

    def test_flash_uses_replacement_decoding_and_normalizes_missing_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project = root / "Demo"
            build_dir = project / "build"
            build_dir.mkdir(parents=True, exist_ok=True)
            (build_dir / "demo.elf").write_text("", encoding="utf-8")

            programmer_bin = root / "workspace" / "toolchains" / "programmer" / "bin"
            programmer_bin.mkdir(parents=True, exist_ok=True)
            (programmer_bin / "STM32_Programmer_CLI.exe").write_text("", encoding="utf-8")

            config = AgentConfig()
            manager = ToolchainManager(config=config, project_root=root)
            adapter = STM32CubeMXAdapter(toolchain_manager=manager)

            list_result = mock.Mock(returncode=0, stdout="", stderr="")
            flash_result = mock.Mock(returncode=1, stdout=None, stderr=None)
            with mock.patch("luxar.platforms.stm32_adapter.subprocess.run", side_effect=[list_result, flash_result]) as run_mock:
                result = adapter.flash(str(project))

            self.assertFalse(result.success)
            self.assertEqual("", result.stdout)
            for call in run_mock.call_args_list:
                self.assertEqual("utf-8", call.kwargs.get("encoding"))
                self.assertEqual("replace", call.kwargs.get("errors"))


class STM32AdapterMonitorTests(unittest.TestCase):
    def test_monitor_auto_detects_usb_serial_port_when_port_is_omitted(self) -> None:
        adapter = STM32CubeMXAdapter()
        port_info = mock.Mock(
            device="COM3",
            description="USB-Enhanced-SERIAL CH343 (COM3)",
            hwid="USB VID:PID=1A86:55D3",
            manufacturer="wch",
            product="USB SERIAL",
        )
        serial_instance = mock.Mock()
        serial_instance.readline.side_effect = [b"ready\r\n"]
        serial_instance.is_open = True

        with mock.patch("serial.Serial", return_value=serial_instance) as serial_cls, \
             mock.patch("serial.tools.list_ports.comports", return_value=[port_info]):
            result = adapter.monitor("/tmp/project", timeout=0.01, lines=1)

        self.assertTrue(result.success)
        self.assertEqual("COM3", result.port)
        self.assertEqual(["ready"], result.lines)
        serial_cls.assert_called_once()
        serial_instance.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
