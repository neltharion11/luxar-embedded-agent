from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luxar.tools.workspace_tool import workspace_hw_probe, workspace_probe, workspace_uart_gate


class HardwareGateToolTests(unittest.TestCase):
    @staticmethod
    def _cm_stub(root: Path):
        cfg = type(
            "Cfg",
            (),
            {
                "toolchains": type(
                    "Toolchains",
                    (),
                    {
                        "root": "toolchains",
                        "cmake": "",
                        "arm_gcc": "",
                        "ninja": "",
                        "openocd": "",
                        "programmer_cli": "",
                    },
                )(),
                "build": type("Build", (), {"toolchain_prefix": "arm-none-eabi-"})(),
            },
        )()
        cm = type("CM", (), {})()
        cm.ensure_default_config = lambda: cfg
        cm.workspace_root = lambda: root / "workspace" / "projects"
        cm.project_root = lambda: root
        return cm

    @staticmethod
    def _create_project(workspace: Path, name: str = "GateDemo", runtime: str = "baremetal") -> Path:
        project = workspace / name
        (project / "App" / "Inc").mkdir(parents=True)
        (project / "App" / "Src").mkdir(parents=True)
        (project / "Core" / "Inc").mkdir(parents=True)
        (project / "Core" / "Inc" / "stm32f1xx_hal_conf.h").write_text(
            """#define HAL_RCC_MODULE_ENABLED
#ifdef HAL_PWR_MODULE_ENABLED
 #include "stm32f1xx_hal_pwr.h"
#endif
""",
            encoding="utf-8",
        )
        (project / "CMakeLists.txt").write_text(
            """target_sources(GateDemo PRIVATE
    ${HAL_DRIVER}/Src/stm32f1xx_hal_pwr.c
)
""",
            encoding="utf-8",
        )
        (project / ".agent_project.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "platform": "stm32firmware",
                    "runtime": runtime,
                    "family": "F1",
                    "mcu": "STM32F103C8",
                }
            ),
            encoding="utf-8",
        )
        return project

    def test_workspace_hw_probe_parses_stlink_programmer_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace" / "projects"
            self._create_project(workspace)
            cm = self._cm_stub(root)
            stdout = """
ST-LINK SN  : W
ST-LINK FW  : V2J47S7
Voltage     : 3.26V
Device ID   : 0x410
Device name : STM32F101/F102/F103 Medium-density
NVM size  : 64 KBytes
Device CPU  : Cortex-M3
0x08000000 : 20004FFF
"""
            proc = type("Proc", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

            with patch("luxar.tools.workspace_tool._get_cm", return_value=cm), patch(
                "luxar.core.toolchain_manager.ToolchainManager.resolve_programmer_cli",
                return_value=str(root / "toolchains" / "programmer" / "bin" / "STM32_Programmer_CLI.exe"),
            ), patch("subprocess.run", return_value=proc) as run_mock:
                result = workspace_hw_probe("GateDemo")

        self.assertTrue(result["success"])
        self.assertEqual("W", result["stlink"]["serial"])
        self.assertEqual("3.26V", result["target"]["voltage"])
        self.assertEqual("0x410", result["target"]["device_id"])
        self.assertEqual("STM32F101/F102/F103 Medium-density", result["target"]["device_name"])
        self.assertEqual("20004FFF", result["readback"][0]["value"])
        run_mock.assert_called_once()
        self.assertIn("-r32", run_mock.call_args.args[0])

    def test_workspace_probe_stlink_is_not_hardware_probe_entrypoint(self) -> None:
        with patch("luxar.tools.workspace_tool.run_probe_project", return_value={"success": False, "probe_type": "stlink"}):
            result = workspace_probe("GateDemo", probe_type="stlink")

        self.assertEqual("stlink", result["probe_type"])
        self.assertFalse(result["success"])

    def test_workspace_uart_gate_generates_confirmed_usart_firmware(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace" / "projects"
            project = self._create_project(workspace)
            cm = self._cm_stub(root)

            with patch("luxar.tools.workspace_tool._get_cm", return_value=cm):
                result = workspace_uart_gate("GateDemo", usart="USART1", tx_pin="PA9", rx_pin="PA10", baudrate=115200)

            source = (project / "App" / "Src" / "app_main.c").read_text(encoding="utf-8")
            header = (project / "App" / "Inc" / "app_main.h").read_text(encoding="utf-8")
            hal_conf = (project / "Core" / "Inc" / "stm32f1xx_hal_conf.h").read_text(encoding="utf-8")
            cmake = (project / "CMakeLists.txt").read_text(encoding="utf-8")

        self.assertTrue(result["success"])
        self.assertIn("USART1", source)
        self.assertIn("GPIO_PIN_9", source)
        self.assertIn("GPIO_PIN_10", source)
        self.assertIn("115200", source)
        self.assertIn("LUXAR_HW_GATE_OK", source)
        self.assertIn("App_DefaultTask", header)
        self.assertIn("HAL_UART_MODULE_ENABLED", hal_conf)
        self.assertIn('stm32f1xx_hal_uart.h', hal_conf)
        self.assertIn("stm32f1xx_hal_uart.c", cmake)

    def test_workspace_uart_gate_rejects_unconfirmed_pin_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace" / "projects"
            self._create_project(workspace)
            cm = self._cm_stub(root)

            with patch("luxar.tools.workspace_tool._get_cm", return_value=cm):
                result = workspace_uart_gate("GateDemo", usart="USART1", tx_pin="PA2", rx_pin="PA3", baudrate=115200)

        self.assertFalse(result["success"])
        self.assertIn("PA9/PA10", result["error"])


if __name__ == "__main__":
    unittest.main()
