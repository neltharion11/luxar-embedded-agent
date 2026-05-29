from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.firmware_library_manager import FirmwareLibraryManager


def _write_real_f1_package(root: Path, name: str) -> Path:
    package = root / "stm32" / name
    required_files = [
        package / "Drivers" / "CMSIS" / "Core" / "Include" / "core_cm3.h",
        package / "Drivers" / "CMSIS" / "Device" / "ST" / "STM32F1xx" / "Include" / "stm32f1xx.h",
        package / "Drivers" / "STM32F1xx_HAL_Driver" / "Inc" / "stm32f1xx_hal.h",
        package / "Drivers" / "STM32F1xx_HAL_Driver" / "Inc" / "stm32f1xx_hal_gpio.h",
        package / "Drivers" / "STM32F1xx_HAL_Driver" / "Src" / "stm32f1xx_hal.c",
    ]
    for file_path in required_files:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("/* real STM32Cube test fixture */\n", encoding="utf-8")
    return package


class FirmwareLibraryManagerTests(unittest.TestCase):
    def test_resolve_stm32_package_prefers_versioned_package_over_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            placeholder = root / "stm32" / "STM32Cube_FW_F1"
            placeholder.mkdir(parents=True)
            (placeholder / "README.md").write_text(
                "Placeholder: replace this folder with a real STM32Cube package.",
                encoding="utf-8",
            )
            versioned = _write_real_f1_package(root, "STM32Cube_FW_F1_V1.8.7")

            resolved = FirmwareLibraryManager(root).resolve_stm32_package("STM32Cube_FW_F1")

            self.assertEqual(versioned.resolve(), resolved)

    def test_placeholder_package_fails_validation_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            placeholder = root / "stm32" / "STM32Cube_FW_F1"
            placeholder.mkdir(parents=True)
            (placeholder / "README.md").write_text(
                "Placeholder: replace this folder with a real STM32Cube package.",
                encoding="utf-8",
            )
            manager = FirmwareLibraryManager(root)

            errors = manager.validate_stm32_package(placeholder)

            self.assertTrue(any("placeholder" in item.lower() for item in errors))
            with self.assertRaises(ValueError):
                manager.stage_stm32_firmware_package(placeholder, root / "project", mcu="STM32F103C8T6")


if __name__ == "__main__":
    unittest.main()
