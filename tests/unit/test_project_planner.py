from __future__ import annotations

import unittest
from unittest.mock import patch

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClientError, LLMResponse
from luxar.core.project_planner import ProjectPlanner
from luxar.models.schemas import DriverBinding, DriverRequirement, PeripheralCapability, ProjectConfig, ProjectPlan


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
        internal = {item.instance for item in plan.internal_peripherals}
        self.assertIn("SPI1", internal)
        self.assertIn("USART2", internal)
        self.assertEqual("SPI1", plan.transport_bindings[0].peripheral)

    def test_internal_peripherals_do_not_generate_external_drivers(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        with patch.object(planner.llm_client, "complete", side_effect=LLMClientError("offline")):
            plan = planner.build_plan(
                project=_fake_project(),
                requirement=(
                    "Use USART2 on PA2 PA3 and TIM3 PWM on PA6 PA7 PB0 for an RGB LED "
                    "rainbow effect."
                ),
            )
        self.assertEqual([], plan.needed_drivers)
        self.assertEqual([], plan.external_devices)
        internal = {(item.interface, item.instance) for item in plan.internal_peripherals}
        self.assertIn(("UART", "USART2"), internal)
        self.assertIn(("TIM", "TIM3"), internal)
        self.assertTrue(any(item.instance == "RGB_LED" for item in plan.board_features))

    def test_chinese_rgb_rainbow_infers_tim3_pwm_without_explicit_pwm_word(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        with patch.object(planner.llm_client, "complete", side_effect=LLMClientError("offline")):
            plan = planner.build_plan(
                project=_fake_project(),
                requirement="使用USART2作为串口调试接口，PA6 PA7 PB0为红绿蓝三色LED灯，请实现RGB彩虹切换效果。",
            )
        self.assertEqual([], plan.needed_drivers)
        tim3 = next(item for item in plan.internal_peripherals if item.instance == "TIM3")
        self.assertEqual("PWM", tim3.mode)
        self.assertEqual({"CH1": "PA6", "CH2": "PA7", "CH3": "PB0"}, tim3.pins)
        rgb = next(item for item in plan.board_features if item.instance == "RGB_LED")
        self.assertEqual({"R": "PA6", "G": "PA7", "B": "PB0"}, rgb.pins)
        self.assertFalse(any("PC13" in hint for hint in plan.peripheral_hints))

    def test_common_internal_interfaces_are_sanitized_out_of_driver_pipeline(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        with patch.object(planner.llm_client, "complete", side_effect=LLMClientError("offline")):
            plan = planner.build_plan(
                project=_fake_project(),
                requirement="Use SPI1, I2C1, ADC sampling with DMA, and CAN echo.",
            )
        self.assertEqual([], plan.needed_drivers)
        internal = {(item.interface, item.instance) for item in plan.internal_peripherals}
        self.assertIn(("SPI", "SPI1"), internal)
        self.assertIn(("I2C", "I2C1"), internal)
        self.assertIn(("ADC", "ADC1"), internal)
        self.assertTrue(any(item.interface == "DMA" for item in plan.internal_peripherals))
        self.assertIn(("CAN", "CAN1"), internal)

    def test_placeholder_and_mcu_peripheral_driver_requirements_are_removed(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        plan = ProjectPlan(
            requirement_summary="Use generated TIM3 PWM and EMB-001.",
            needed_drivers=[
                DriverRequirement(chip="TIM3", interface="PWM", confidence=0.8),
                DriverRequirement(chip="EMB-001", interface="SPI", confidence=0.8),
            ],
        )
        sanitized = planner.sanitize_plan(project=_fake_project(), requirement=plan.requirement_summary, plan=plan)
        self.assertEqual([], sanitized.needed_drivers)
        self.assertTrue(any(item.instance == "TIM3" for item in sanitized.internal_peripherals))
        self.assertTrue(any("placeholder" in item.lower() for item in sanitized.risk_notes))

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

    def test_sanitize_plan_merges_duplicate_rgb_features_and_ch1116_bindings(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        plan = ProjectPlan(
            requirement_summary="RGB breathing with CH1116 OLED status over I2C.",
            needed_drivers=[DriverRequirement(chip="CH1116", interface="I2C", vendor="unknown", device="oled display controller")],
            board_features=[
                PeripheralCapability(interface="BOARD", instance="RGB_LED", mode="PWM", pins={"R": "PA6", "G": "PA7", "B": "PB0"}, dependencies=["TIM3"]),
                PeripheralCapability(interface="BOARD", instance="RGB_LED", mode="pwm_output", pins={}, dependencies=["TIM", "GPIO"]),
            ],
            transport_bindings=[
                DriverBinding(device="CH1116 OLED", driver="ch1116", transport="I2C", peripheral="I2C1"),
            ],
        )
        sanitized = planner.sanitize_plan(project=_fake_project(), requirement=plan.requirement_summary, plan=plan)
        self.assertEqual(1, len([item for item in sanitized.board_features if item.instance == "RGB_LED"]))
        self.assertEqual({"R": "PA6", "G": "PA7", "B": "PB0"}, sanitized.board_features[0].pins)
        self.assertEqual(1, len([item for item in sanitized.transport_bindings if item.transport == "I2C" and item.peripheral == "I2C1"]))

    def test_chinese_oled_rgb_requirement_fallback_extracts_ch1116_and_rgb(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        with patch.object(planner.llm_client, "complete", side_effect=LLMClientError("offline")):
            plan = planner.build_plan(
                project=_fake_project(),
                requirement="在本项目中RST不需要配置，模块是上电自动重启，驱动库中可以写一下相关的函数，生成 CH1116 OLED 的 I²C 驱动代码，并整合到你的呼吸灯项目中，实现在 OLED 上显示 RGB 状态信息。",
            )
        self.assertTrue(plan.used_fallback)
        self.assertTrue(any(item.chip == "CH1116" and item.interface == "I2C" for item in plan.needed_drivers))
        self.assertTrue(any(item.instance == "RGB_LED" for item in plan.board_features))
        self.assertTrue(any(item.transport == "I2C" and item.peripheral == "I2C1" for item in plan.transport_bindings))

    def test_sanitize_plan_merges_duplicate_driver_requirements_by_chip_and_interface(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        plan = ProjectPlan(
            requirement_summary="CH1116 OLED status over I2C.",
            needed_drivers=[
                DriverRequirement(chip="CH1116", interface="I2C", vendor="generic", device="OLED Display", confidence=0.8, rationale="first"),
            ],
            external_devices=[
                DriverRequirement(chip="CH1116", interface="I2C", vendor="Sino Wealth", device="I2C OLED Display 128x64", confidence=0.9, rationale="second"),
            ],
        )
        sanitized = planner.sanitize_plan(project=_fake_project(), requirement=plan.requirement_summary, plan=plan)
        self.assertEqual(1, len(sanitized.needed_drivers))
        self.assertEqual("CH1116", sanitized.needed_drivers[0].chip)
        self.assertEqual("I2C", sanitized.needed_drivers[0].interface)
        self.assertEqual("I2C OLED Display 128x64", sanitized.needed_drivers[0].device)
        self.assertEqual("Sino Wealth", sanitized.needed_drivers[0].vendor)

    def test_sanitize_plan_drops_redundant_inferred_timer_and_generic_led_gpio(self) -> None:
        planner = ProjectPlanner(AgentConfig())
        plan = ProjectPlan(
            requirement_summary="保留现有呼吸灯效果，不需要接RST脚，CH1116 OLED 上电自动复位。请给这个项目补一个 I2C OLED 驱动，并在屏幕上实时显示当前 RGB 呼吸状态。",
            internal_peripherals=[
                PeripheralCapability(interface="TIM", instance="TIM2", mode="PWM", pins={"CH1": "PA0", "CH2": "PA1", "CH3": "PA2"}),
                PeripheralCapability(interface="GPIO", instance="LED pins (three GPIOs or PWM outputs)", mode="mixed", pins={"R": "PA0", "G": "PA1", "B": "PA2"}),
            ],
            board_features=[
                PeripheralCapability(interface="BOARD", instance="RGB_LED", mode="pwm_output", pins={"R": "PA0", "G": "PA1", "B": "PA2"}),
            ],
            needed_drivers=[
                DriverRequirement(chip="CH1116", interface="I2C", device="OLED Display", confidence=0.9),
            ],
        )
        sanitized = planner.sanitize_plan(project=_fake_project(), requirement=plan.requirement_summary, plan=plan)
        timers = [item.instance for item in sanitized.internal_peripherals if item.interface == "TIM"]
        self.assertIn("TIM2", timers)
        self.assertNotIn("TIM3", timers)
        self.assertFalse(any(item.interface == "GPIO" and "LED" in item.instance for item in sanitized.internal_peripherals))


if __name__ == "__main__":
    unittest.main()
