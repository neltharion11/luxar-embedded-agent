from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luxar.core.app_generator import AppGenerator
from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClientError
from luxar.models.schemas import PeripheralCapability, ProjectConfig, ProjectPlan


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

    def test_rgb_rainbow_fallback_generates_working_pwm_app(self) -> None:
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
                requirement_summary="RGB rainbow on PA6 PA7 PB0.",
                features=["RGB rainbow cycling using hardware PWM on TIM3."],
                internal_peripherals=[
                    PeripheralCapability(interface="TIM", instance="TIM3", mode="PWM", pins={"CH1": "PA6", "CH2": "PA7", "CH3": "PB0"}),
                ],
                board_features=[
                    PeripheralCapability(interface="BOARD", instance="RGB_LED", mode="pwm_output", pins={"R": "PA6", "G": "PA7", "B": "PB0"}),
                ],
                app_behavior_summary="Sweep rainbow hue and write UART status.",
            )
            with patch.object(generator.llm_client, "complete", side_effect=LLMClientError("offline")):
                result = generator.generate_app(project=project, project_plan=plan, installed_drivers=[])

            self.assertTrue(result.success)
            self.assertTrue(result.used_fallback)
            content = (project_root / "App" / "Src" / "app_main.c").read_text(encoding="utf-8")
            self.assertIn("hsv_to_rgb", content)
            self.assertIn("luxar_rgb_pwm_set(scale8_to_pwm(red)", content)
            self.assertIn("luxar_uart_write", content)
            self.assertNotIn("TODO(luxar)", content)
            self.assertNotIn("math.h", content)
            self.assertNotIn("sin(", content)

    def test_unknown_rgb_helper_triggers_verified_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "Demo"
            project_root.mkdir(parents=True, exist_ok=True)
            project = ProjectConfig(name="Demo", path=str(project_root), project_mode="firmware", mcu="STM32F103C8T6")
            generator = AppGenerator(AgentConfig())
            plan = ProjectPlan(
                requirement_summary="RGB rainbow on PA6 PA7 PB0.",
                features=["RGB rainbow cycling using hardware PWM on TIM3."],
                internal_peripherals=[
                    PeripheralCapability(interface="TIM", instance="TIM3", mode="PWM", pins={"CH1": "PA6", "CH2": "PA7", "CH3": "PB0"}),
                ],
                board_features=[
                    PeripheralCapability(interface="BOARD", instance="RGB_LED", mode="pwm_output", pins={"R": "PA6", "G": "PA7", "B": "PB0"}),
                ],
                app_behavior_summary="Sweep rainbow hue.",
            )
            llm_content = """```c header
#ifndef APP_MAIN_H
#define APP_MAIN_H
void app_main_init(void);
void app_main_loop(void);
#endif
```
```c source
#include "app_main.h"
void app_main_init(void) { luxar_rgb_set(0, 0, 0); }
void app_main_loop(void) {}
```"""
            with patch.object(generator.llm_client, "complete", return_value=type("Resp", (), {"content": llm_content})()):
                result = generator.generate_app(project=project, project_plan=plan, installed_drivers=[])

            self.assertTrue(result.used_fallback)
            content = (project_root / "App" / "Src" / "app_main.c").read_text(encoding="utf-8")
            self.assertIn("luxar_rgb_pwm_set", content)
            self.assertNotIn("luxar_rgb_set", content)

    def test_rgb_oled_status_fallback_uses_installed_driver_and_i2c_glue(self) -> None:
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
                requirement_summary="Show RGB status on CH1116 OLED over I2C.",
                features=["RGB LED breathing light with PWM", "OLED display of RGB status"],
                internal_peripherals=[
                    PeripheralCapability(interface="TIM", instance="TIM3", mode="PWM", pins={"CH1": "PA6", "CH2": "PA7", "CH3": "PB0"}),
                    PeripheralCapability(interface="I2C", instance="I2C1", mode="master", pins={"SCL": "PB6", "SDA": "PB7"}),
                ],
                board_features=[
                    PeripheralCapability(interface="BOARD", instance="RGB_LED", mode="pwm_output", pins={"R": "PA6", "G": "PA7", "B": "PB0"}),
                ],
                transport_bindings=[
                    {"device": "ch1116", "driver": "ch1116", "transport": "I2C", "peripheral": "I2C1"},
                ],
                app_behavior_summary="Continuously update OLED with RGB state.",
            )
            with patch.object(generator.llm_client, "complete", side_effect=LLMClientError("offline")):
                result = generator.generate_app(project=project, project_plan=plan, installed_drivers=["oled_128x64"])

            self.assertTrue(result.success)
            self.assertTrue(result.used_fallback)
            content = (project_root / "App" / "Src" / "app_main.c").read_text(encoding="utf-8")
            self.assertIn('#include "oled_128x64.h"', content)
            self.assertIn("ch1116_init", content)
            self.assertIn("luxar_i2c_txrx", content)
            self.assertIn("oled_write_bar", content)

    def test_oled_plan_falls_back_when_llm_guesses_missing_driver_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "Demo"
            project_root.mkdir(parents=True, exist_ok=True)
            driver_inc = project_root / "App" / "Drivers" / "oled_128x64" / "Inc"
            driver_inc.mkdir(parents=True, exist_ok=True)
            (driver_inc / "oled_128x64.h").write_text("typedef struct { int dummy; } ch1116_hal_t;\n", encoding="utf-8")
            project = ProjectConfig(
                name="Demo",
                path=str(project_root),
                project_mode="firmware",
                mcu="STM32F103C8T6",
            )
            generator = AppGenerator(AgentConfig())
            plan = ProjectPlan(
                requirement_summary="Show RGB status on CH1116 OLED over I2C.",
                features=["RGB LED breathing light with PWM", "OLED display of RGB status"],
                internal_peripherals=[
                    PeripheralCapability(interface="TIM", instance="TIM3", mode="PWM", pins={"CH1": "PA6", "CH2": "PA7", "CH3": "PB0"}),
                    PeripheralCapability(interface="I2C", instance="I2C1", mode="master", pins={"SCL": "PB6", "SDA": "PB7"}),
                ],
                board_features=[
                    PeripheralCapability(interface="BOARD", instance="RGB_LED", mode="pwm_output", pins={"R": "PA6", "G": "PA7", "B": "PB0"}),
                ],
                transport_bindings=[
                    {"device": "ch1116", "driver": "ch1116", "transport": "I2C", "peripheral": "I2C1"},
                ],
                app_behavior_summary="Continuously update OLED with RGB state.",
            )
            llm_content = """```c header
#ifndef APP_MAIN_H
#define APP_MAIN_H
void app_main_init(void);
void app_main_loop(void);
#endif
```
```c source
#include "app_main.h"
#include "luxar_hardware.h"
#include "ch1116.h"
void app_main_init(void) {}
void app_main_loop(void) {}
```"""
            with patch.object(generator.llm_client, "complete", return_value=type("Resp", (), {"content": llm_content})()):
                result = generator.generate_app(project=project, project_plan=plan, installed_drivers=["oled_128x64"])

            self.assertTrue(result.success)
            self.assertTrue(result.used_fallback)
            content = (project_root / "App" / "Src" / "app_main.c").read_text(encoding="utf-8")
            self.assertIn('#include "oled_128x64.h"', content)
            self.assertNotIn('#include "ch1116.h"', content)

    def test_project_driver_header_discovery_overrides_logical_driver_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "Demo"
            project_root.mkdir(parents=True, exist_ok=True)
            driver_inc = project_root / "App" / "Drivers" / "oled_128x64" / "Inc"
            driver_inc.mkdir(parents=True, exist_ok=True)
            (driver_inc / "oled_128x64.h").write_text("typedef struct { int dummy; } ch1116_hal_t;\n", encoding="utf-8")
            project = ProjectConfig(
                name="Demo",
                path=str(project_root),
                project_mode="firmware",
                mcu="STM32F103C8T6",
            )
            generator = AppGenerator(AgentConfig())
            plan = ProjectPlan(
                requirement_summary="Show RGB status on CH1116 OLED over I2C.",
                features=["RGB LED breathing light with PWM", "OLED display of RGB status"],
                internal_peripherals=[
                    PeripheralCapability(interface="TIM", instance="TIM3", mode="PWM", pins={"CH1": "PA6", "CH2": "PA7", "CH3": "PB0"}),
                    PeripheralCapability(interface="I2C", instance="I2C1", mode="master", pins={"SCL": "PB6", "SDA": "PB7"}),
                ],
                board_features=[
                    PeripheralCapability(interface="BOARD", instance="RGB_LED", mode="pwm_output", pins={"R": "PA6", "G": "PA7", "B": "PB0"}),
                ],
                transport_bindings=[
                    {"device": "ch1116", "driver": "ch1116", "transport": "I2C", "peripheral": "I2C1"},
                ],
                app_behavior_summary="Continuously update OLED with RGB state.",
            )
            with patch.object(generator.llm_client, "complete", side_effect=LLMClientError("offline")):
                result = generator.generate_app(project=project, project_plan=plan, installed_drivers=["ch1116"])

            self.assertTrue(result.success)
            self.assertTrue(result.used_fallback)
            content = (project_root / "App" / "Src" / "app_main.c").read_text(encoding="utf-8")
            self.assertIn('#include "oled_128x64.h"', content)
            self.assertNotIn('#include "ch1116.h"', content)

    def test_known_rgb_oled_plan_uses_deterministic_generator_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir) / "Demo"
            project_root.mkdir(parents=True, exist_ok=True)
            driver_inc = project_root / "App" / "Drivers" / "oled_128x64" / "Inc"
            driver_inc.mkdir(parents=True, exist_ok=True)
            (driver_inc / "oled_128x64.h").write_text("typedef struct { int dummy; } ch1116_hal_t;\n", encoding="utf-8")
            project = ProjectConfig(
                name="Demo",
                path=str(project_root),
                project_mode="firmware",
                mcu="STM32F103C8T6",
            )
            generator = AppGenerator(AgentConfig())
            plan = ProjectPlan(
                requirement_summary="Show RGB status on CH1116 OLED over I2C.",
                features=["RGB LED breathing light with PWM", "OLED display of RGB status"],
                internal_peripherals=[
                    PeripheralCapability(interface="TIM", instance="TIM3", mode="PWM", pins={"CH1": "PA6", "CH2": "PA7", "CH3": "PB0"}),
                    PeripheralCapability(interface="I2C", instance="I2C1", mode="master", pins={"SCL": "PB6", "SDA": "PB7"}),
                ],
                board_features=[
                    PeripheralCapability(interface="BOARD", instance="RGB_LED", mode="pwm_output", pins={"R": "PA6", "G": "PA7", "B": "PB0"}),
                ],
                transport_bindings=[
                    {"device": "ch1116", "driver": "ch1116", "transport": "I2C", "peripheral": "I2C1"},
                ],
                app_behavior_summary="Continuously update OLED with RGB state.",
            )
            with patch.object(generator.llm_client, "complete") as complete_mock:
                result = generator.generate_app(project=project, project_plan=plan, installed_drivers=["ch1116"])

            self.assertTrue(result.success)
            self.assertTrue(result.used_fallback)
            complete_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
