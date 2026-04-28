from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.project_manager import ProjectManager


class ProjectImportTests(unittest.TestCase):
    def test_import_existing_directory_creates_workspace_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            source = Path(tmpdir) / "external_project"
            source.mkdir(parents=True, exist_ok=True)
            (source / "App").mkdir()
            (source / "demo.ioc").write_text("# demo", encoding="utf-8")

            manager = ProjectManager(str(workspace))
            project = manager.import_project(
                source_path=str(source),
                name="ImportedDemo",
                mcu="STM32F103C8T6",
                project_mode="cubemx",
            )

            self.assertEqual("ImportedDemo", project.name)
            self.assertEqual(str(source.resolve()), project.path)
            self.assertTrue((workspace / "ImportedDemo" / ".agent_project.json").exists())


if __name__ == "__main__":
    unittest.main()
