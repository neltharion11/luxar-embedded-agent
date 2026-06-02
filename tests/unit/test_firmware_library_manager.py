from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from luxar.core.firmware_library_manager import FirmwareLibraryManager
from luxar.tools.workspace_tool import workspace_create_project


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
    template_dir = package / "Drivers" / "CMSIS" / "Device" / "ST" / "STM32F1xx" / "Source" / "Templates"
    (template_dir / "gcc" / "linker").mkdir(parents=True, exist_ok=True)
    (template_dir / "gcc" / "startup_stm32f103xb.s").write_text("/* startup */\n", encoding="utf-8")
    (template_dir / "gcc" / "linker" / "STM32F103XB_FLASH.ld").write_text("/* linker */\n", encoding="utf-8")
    (template_dir / "system_stm32f1xx.c").write_text("/* system */\n", encoding="utf-8")
    return package


def _write_real_package(root: Path, family: str, name: str, device_define: str, core_header: str) -> Path:
    family_lower = family.lower()
    package = root / "stm32" / name
    device_dir = package / "Drivers" / "CMSIS" / "Device" / "ST" / f"STM32{family}xx"
    hal_dir = package / "Drivers" / f"STM32{family}xx_HAL_Driver"
    required_files = [
        package / "Drivers" / "CMSIS" / "Core" / "Include" / core_header,
        device_dir / "Include" / f"stm32{family_lower}xx.h",
        hal_dir / "Inc" / f"stm32{family_lower}xx_hal.h",
        hal_dir / "Inc" / f"stm32{family_lower}xx_hal_gpio.h",
        hal_dir / "Src" / f"stm32{family_lower}xx_hal.c",
    ]
    for file_path in required_files:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("/* real STM32Cube test fixture */\n", encoding="utf-8")
    template_dir = device_dir / "Source" / "Templates"
    (template_dir / "gcc" / "linker").mkdir(parents=True, exist_ok=True)
    (template_dir / "gcc" / f"startup_{device_define.lower()}.s").write_text("/* startup */\n", encoding="utf-8")
    (template_dir / "gcc" / "linker" / f"{device_define.upper()}_FLASH.ld").write_text("/* linker */\n", encoding="utf-8")
    (template_dir / f"system_stm32{family_lower}xx.c").write_text("/* system */\n", encoding="utf-8")
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

    def test_resolve_package_for_mcu_finds_matching_family_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            versioned = _write_real_f1_package(root, "STM32Cube_FW_F1_V1.8.7")

            resolved = FirmwareLibraryManager(root).resolve_stm32_package_for_mcu("STM32F103C8T6")

            self.assertEqual(versioned.resolve(), resolved)

    def test_build_profile_for_f4_uses_family_specific_package_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package = _write_real_package(root, "F4", "STM32Cube_FW_F4_V1.28.0", "STM32F407xx", "core_cm4.h")

            profile = FirmwareLibraryManager(root).build_stm32_profile("STM32F407VG", package)

            self.assertEqual("F4", profile["family"])
            self.assertEqual("STM32F407xx", profile["device_define"])
            self.assertEqual("STM32F4xx", profile["cmsis_device_dir"])
            self.assertEqual("STM32F4xx_HAL_Driver", profile["hal_driver_dir"])
            self.assertEqual("startup_stm32f407xx.s", profile["startup_file"])
            self.assertEqual("STM32F407XX_FLASH.ld", profile["linker_script"])
            self.assertIn("cortex-m4", profile["cpu_flags"])

    def test_missing_family_package_reports_expected_cube_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manager = FirmwareLibraryManager(root)

            with self.assertRaises(FileNotFoundError) as ctx:
                manager.build_stm32_profile("STM32G071RB")

            self.assertIn("STM32Cube_FW_G0", str(ctx.exception))

    def test_workspace_create_project_fails_when_firmware_package_for_mcu_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "projects"
            firmware_root = root / "firmware_library"
            firmware_root.mkdir(parents=True)
            cm = SimpleNamespace(
                ensure_default_config=lambda: SimpleNamespace(stm32=SimpleNamespace(firmware_package="")),
                workspace_root=lambda: workspace,
                firmware_library_root=lambda: firmware_root,
            )

            with patch("luxar.tools.workspace_tool._get_cm", return_value=cm):
                result = workspace_create_project(
                    name="MissingG0",
                    mcu="STM32G071RB",
                    platform="stm32firmware",
                    runtime="baremetal",
                )

            self.assertFalse(result["success"])
            self.assertIn("STM32Cube_FW_G0", result["error"])
            self.assertFalse((workspace / "MissingG0").exists())


if __name__ == "__main__":
    unittest.main()
