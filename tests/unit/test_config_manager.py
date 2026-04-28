from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.config_manager import ConfigManager


class ConfigManagerTests(unittest.TestCase):
    def test_workspace_root_resolves_relative_to_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_dir = root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "luxar.yaml"
            config_path.write_text(
                "agent:\n"
                "  workspace: ./workspace/projects\n"
                "  driver_library: ./workspace/driver_library\n"
                "  skill_library: ./skill_library\n"
                "  firmware_library: ./vendor/firmware_library\n",
                encoding="utf-8",
            )

            manager = ConfigManager(config_path)

            self.assertEqual((root / "workspace" / "projects").resolve(), manager.workspace_root())
            self.assertEqual((root / "workspace" / "driver_library").resolve(), manager.driver_library_root())
            self.assertEqual((root / "skill_library").resolve(), manager.skill_library_root())
            self.assertEqual((root / "vendor" / "firmware_library").resolve(), manager.firmware_library_root())


if __name__ == "__main__":
    unittest.main()

