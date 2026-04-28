from __future__ import annotations

import unittest
from unittest.mock import patch

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClientError, LLMResponse
from luxar.core.project_planner import ProjectPlanner
from luxar.models.schemas import ProjectConfig


def _fake_project() -> ProjectConfig:
    return ProjectConfig(
        name="BlinkTest",
        path="/fake/workspace/BlinkTest",
        project_mode="cubemx",
        mcu="STM32F103C8T6",
    )


class ProjectPlannerTests(unittest.TestCase):
    def test_led_blink_requirement_generates_structured_plan(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        with patch.object(planner.llm_client, "complete", side_effect=LLMClientError("offline")):
            plan = planner.build_plan(
                project=_fake_project(),
                requirement="Blink LED once per second.",
            )
        self.assertTrue(plan.requirement_summary)
        self.assertTrue(any("Blink" in item or "LED" in item for item in plan.features))
        self.assertTrue(plan.cubemx_or_firmware_actions)
        self.assertTrue(plan.used_fallback)

    def test_uart_sensor_requirement_extracts_driver_candidates_and_hints(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        plan = planner.build_plan(
            project=_fake_project(),
            requirement="Read BMI270 over SPI, poll it periodically, and print results over UART.",
        )
        self.assertEqual(1, len(plan.needed_drivers))
        self.assertEqual("BMI270", plan.needed_drivers[0].chip)
        self.assertEqual("SPI", plan.needed_drivers[0].interface)
        self.assertTrue(any("UART" in item.upper() for item in plan.peripheral_hints))
        self.assertTrue(any("SPI" in item.upper() for item in plan.peripheral_hints))

    def test_invalid_llm_json_falls_back(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        with patch.object(
            planner.llm_client,
            "complete",
            return_value=LLMResponse(provider="deepseek", model="x", content="{bad json", raw={}),
        ):
            plan = planner.build_plan(
                project=_fake_project(),
                requirement="Blink LED once per second.",
            )
        self.assertTrue(plan.used_fallback)
        self.assertTrue(plan.features)

    def test_multiple_driver_mentions_are_extracted(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        plan = planner.build_plan(
            project=_fake_project(),
            requirement="Poll BMI270 over SPI and read SHT31 over I2C while logging status over UART every second.",
        )
        self.assertEqual(2, len(plan.needed_drivers))
        pairs = {(item.chip, item.interface) for item in plan.needed_drivers}
        self.assertIn(("BMI270", "SPI"), pairs)
        self.assertIn(("SHT31", "I2C"), pairs)
        self.assertTrue(any("UART" in item.upper() for item in plan.peripheral_hints))

    def test_document_context_is_carried_into_plan(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        plan = planner.build_plan(
            project=_fake_project(),
            requirement="Read BMI270 over SPI.",
            document_context="BMI270 register map includes CHIP_ID and status register details.",
        )
        self.assertIn("BMI270", plan.document_context_summary)


if __name__ == "__main__":
    unittest.main()
