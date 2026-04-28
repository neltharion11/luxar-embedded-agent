from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luxar.core.app_generator import AppGenerator
from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClientError
from luxar.models.schemas import ProjectConfig, ProjectPlan


class AppGeneratorTests(unittest.TestCase):
    def test_generate_app_falls_back_when_llm_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "Demo"
            project_root.mkdir(parents=True, exist_ok=True)
            project = ProjectConfig(
                name="Demo",
                path=str(project_root),
                project_mode="firmware",
                mcu="STM32F103C8T6",
            )
            generator = AppGenerator(AgentConfig())
            plan = ProjectPlan(
                requirement_summary="Blink LED once per second and print Hello Agent on UART.",
                features=["Blink an LED from the application loop.", "Emit UART log output from the application layer."],
                peripheral_hints=["GPIO output required for an LED indicator.", "UART TX path is required for textual status output."],
                cubemx_or_firmware_actions=["Configure one GPIO pin as an output for the LED in CubeMX.", "Enable one USART/UART peripheral for TX in CubeMX."],
                app_behavior_summary="Favor periodic, cadence-driven logic in app_main_loop while keeping hardware bindings explicit TODOs.",
                risk_notes=["LED pin is not specified.", "UART instance is not specified."],
                used_fallback=True,
            )
            with patch.object(
                generator.llm_client,
                "complete",
                side_effect=LLMClientError("Missing API key."),
            ):
                result = generator.generate_app(
                    project=project,
                    project_plan=plan,
                    installed_drivers=[],
                )

            self.assertTrue(result.success)
            self.assertTrue(result.used_fallback)
            self.assertTrue((project_root / "App" / "Inc" / "app_main.h").exists())
            self.assertTrue((project_root / "App" / "Src" / "app_main.c").exists())
            content = (project_root / "App" / "Src" / "app_main.c").read_text(encoding="utf-8")
            self.assertIn("TODO(luxar)", content)
            self.assertIn("Installed drivers: none", content)
            self.assertNotIn("PA5", content)


if __name__ == "__main__":
    unittest.main()
