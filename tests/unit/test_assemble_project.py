from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from luxar.core.assembler import Assembler
from luxar.core.driver_library import DriverLibrary
from luxar.core.project_manager import ProjectManager
from luxar.models.schemas import DriverMetadata, PeripheralCapability, ProjectConfig, ProjectPlan
from luxar.tools.assemble_project import run_assemble_project


class AssembleProjectTests(unittest.TestCase):
    def test_assemble_can_install_stored_driver_into_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "projects"
            firmware_root = root / "firmware_library"
            driver_root = root / "driver_library"
            firmware_root.mkdir(parents=True, exist_ok=True)

            manager = ProjectManager(str(workspace))
            project = manager.create_project(
                name="DemoProject",
                mcu="STM32F103C8T6",
                project_mode="cubemx",
            )

            source_dir = root / "generated_driver"
            source_dir.mkdir(parents=True, exist_ok=True)
            header = source_dir / "bmi270.h"
            source = source_dir / "bmi270.c"
            header.write_text("int bmi270_init(void);\n", encoding="utf-8")
            source.write_text("int bmi270_init(void) { return 0; }\n", encoding="utf-8")

            library = DriverLibrary(driver_root)
            library.store_driver(
                DriverMetadata(
                    name="bmi270",
                    protocol="SPI",
                    chip="BMI270",
                    vendor="bosch",
                    device="bmi270",
                    path=str(source),
                    header_path=str(header),
                    source_path=str(source),
                    review_passed=True,
                )
            )

            result = run_assemble_project(
                project=project,
                firmware_library_root=str(firmware_root),
                driver_library_root=str(driver_root),
                drivers=["bmi270"],
            )

            installed_header = Path(project.path) / "App" / "Drivers" / "bmi270" / "Inc" / "bmi270.h"
            installed_source = Path(project.path) / "App" / "Drivers" / "bmi270" / "Src" / "bmi270.c"
            self.assertTrue(installed_header.exists())
            self.assertTrue(installed_source.exists())
            self.assertEqual(1, len(result["installed_drivers"]))
            self.assertEqual("bmi270", result["installed_drivers"][0]["name"])
            cmake_text = (Path(project.path) / "CMakeLists.txt").read_text(encoding="utf-8")
            self.assertNotIn("App/Drivers/*", cmake_text)
            self.assertIn('"App/Drivers/bmi270/Src/bmi270.c"', cmake_text)

            manifest = json.loads((Path(project.path) / "LUXAR_BUILD_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(["App/Drivers/bmi270/Src/bmi270.c"], manifest["driver_sources"])
            self.assertIn("App/Drivers/bmi270/Inc", manifest["include_dirs"])

    def test_assemble_aligns_reused_driver_source_include_with_installed_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "projects"
            firmware_root = root / "firmware_library"
            driver_root = root / "driver_library"
            firmware_root.mkdir(parents=True, exist_ok=True)

            manager = ProjectManager(str(workspace))
            project = manager.create_project(
                name="DemoProject",
                mcu="STM32F103C8T6",
                project_mode="cubemx",
            )

            source_dir = root / "generated_driver"
            source_dir.mkdir(parents=True, exist_ok=True)
            header = source_dir / "oled_128x64.h"
            source = source_dir / "oled_128x64.c"
            header.write_text("int ch1116_init(void);\n", encoding="utf-8")
            source.write_text('#include "oled_ch1116.h"\nint ch1116_init(void){return 0;}\n', encoding="utf-8")

            DriverLibrary(driver_root).store_driver(
                DriverMetadata(
                    name="oled_128x64",
                    protocol="I2C",
                    chip="CH1116",
                    vendor="unknown",
                    device="oled 128x64",
                    path=str(source),
                    header_path=str(header),
                    source_path=str(source),
                    review_passed=True,
                )
            )

            run_assemble_project(
                project=project,
                firmware_library_root=str(firmware_root),
                driver_library_root=str(driver_root),
                drivers=["oled_128x64"],
            )

            installed_source = Path(project.path) / "App" / "Drivers" / "oled_128x64" / "Src" / "oled_128x64.c"
            self.assertIn('#include "oled_128x64.h"', installed_source.read_text(encoding="utf-8"))

    def test_firmware_cmake_uses_manifest_sources_not_driver_glob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project = ProjectConfig(
                name="FirmwareDemo",
                path=tmpdir,
                project_mode="firmware",
                mcu="STM32F103C8T6",
            )

            created = Assembler().assemble_stm32_firmware_project(
                project,
                firmware_package="STM32Cube_FW_F1_V1.8.7",
                stm32_family="F1",
                build_context={"family_define": "STM32F1xx"},
            )

            self.assertTrue((Path(tmpdir) / "LUXAR_BUILD_MANIFEST.json").exists())
            cmake_text = (Path(tmpdir) / "CMakeLists.txt").read_text(encoding="utf-8")
            self.assertNotIn("file(GLOB APP_DRIVER_SOURCES", cmake_text)
            self.assertNotIn("App/Drivers/*", cmake_text)
            self.assertIn("STM32F103xB", cmake_text)
            toolchain_text = (Path(tmpdir) / "cmake" / "toolchain-arm-none-eabi.cmake").read_text(encoding="utf-8")
            self.assertIn("-Wl,--gc-sections", toolchain_text)
            self.assertIn("Core/Src/luxar_hardware.c", cmake_text)
            self.assertTrue(any(path.endswith("LUXAR_BUILD_MANIFEST.json") for path in created))

    def test_firmware_scaffold_renders_internal_uart_and_pwm_glue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project = ProjectConfig(
                name="FirmwareDemo",
                path=tmpdir,
                project_mode="firmware",
                mcu="STM32F103C8T6",
            )
            plan = ProjectPlan(
                requirement_summary="USART2 debug and TIM3 RGB PWM.",
                internal_peripherals=[
                    PeripheralCapability(interface="UART", instance="USART2", mode="async", pins={"TX": "PA2", "RX": "PA3"}),
                    PeripheralCapability(interface="TIM", instance="TIM3", mode="PWM", pins={"CH1": "PA6", "CH2": "PA7", "CH3": "PB0"}),
                ],
                board_features=[
                    PeripheralCapability(interface="BOARD", instance="RGB_LED", mode="pwm_output", pins={"R": "PA6", "G": "PA7", "B": "PB0"}),
                ],
            )

            Assembler().assemble_stm32_firmware_project(
                project,
                firmware_package="STM32Cube_FW_F1_V1.8.7",
                stm32_family="F1",
                build_context={"family_define": "STM32F1xx"},
                project_plan=plan,
            )

            hardware = (Path(tmpdir) / "Core" / "Src" / "luxar_hardware.c").read_text(encoding="utf-8")
            header = (Path(tmpdir) / "Core" / "Inc" / "luxar_hardware.h").read_text(encoding="utf-8")
            self.assertIn("UART_HandleTypeDef huart2", hardware)
            self.assertIn("TIM_HandleTypeDef htim3", hardware)
            self.assertIn("MX_USART2_UART_Init", hardware)
            self.assertIn("MX_TIM3_PWM_Init", hardware)
            self.assertIn("HAL_TIM_PWM_Start(&htim3, TIM_CHANNEL_3)", hardware)
            self.assertIn("luxar_rgb_pwm_set", hardware)
            self.assertIn("void LuxarHardwareInit(void)", hardware)
            self.assertIn("void LuxarHardwareInit(void);", header)

    def test_firmware_scaffold_renders_i2c1_glue_and_hal_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project = ProjectConfig(
                name="FirmwareDemo",
                path=tmpdir,
                project_mode="firmware",
                mcu="STM32F103C8T6",
            )
            plan = ProjectPlan(
                requirement_summary="CH1116 OLED over I2C1.",
                internal_peripherals=[
                    PeripheralCapability(interface="I2C", instance="I2C1", mode="master", pins={"SCL": "PB6", "SDA": "PB7"}, frequency="400 kHz"),
                ],
            )

            Assembler().assemble_stm32_firmware_project(
                project,
                firmware_package="STM32Cube_FW_F1_V1.8.7",
                stm32_family="F1",
                build_context={"family_define": "STM32F1xx"},
                project_plan=plan,
            )

            hardware = (Path(tmpdir) / "Core" / "Src" / "luxar_hardware.c").read_text(encoding="utf-8")
            hal_conf = (Path(tmpdir) / "Core" / "Inc" / "stm32f1xx_hal_conf.h").read_text(encoding="utf-8")
            self.assertIn("I2C_HandleTypeDef hi2c1", hardware)
            self.assertIn("MX_I2C1_Init", hardware)
            self.assertIn("HAL_I2C_Master_Transmit(&hi2c1", hardware)
            self.assertIn("GPIO_MODE_AF_OD", hardware)
            self.assertIn("HAL_I2C_MODULE_ENABLED", hal_conf)


if __name__ == "__main__":
    unittest.main()

