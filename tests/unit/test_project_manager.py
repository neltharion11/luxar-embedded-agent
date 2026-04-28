from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.project_manager import ProjectManager


class ProjectManagerTests(unittest.TestCase):
    def test_create_project_writes_default_clang_tidy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ProjectManager(tmpdir)
            project = manager.create_project(
                name="DemoProject",
                mcu="STM32F103C8T6",
            )

            clang_tidy = Path(project.path) / ".clang-tidy"
            self.assertTrue(clang_tidy.exists())
            self.assertIn("clang-analyzer-*", clang_tidy.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


