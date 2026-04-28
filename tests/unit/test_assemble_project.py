from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.driver_library import DriverLibrary
from luxar.core.project_manager import ProjectManager
from luxar.models.schemas import DriverMetadata
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


if __name__ == "__main__":
    unittest.main()

