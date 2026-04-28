from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luxar.core.config_manager import ConfigManager, _discover_project_root


class ConfigManagerTests(unittest.TestCase):
    def test_default_config_path_is_anchored_to_repo_root(self) -> None:
        manager = ConfigManager()
        expected_root = _discover_project_root()

        self.assertEqual((expected_root / "config" / "luxar.yaml").resolve(), manager.config_path.resolve())
        self.assertEqual(expected_root.resolve(), manager.project_root())
        self.assertEqual((expected_root / "workspace" / "projects").resolve(), manager.workspace_root())

    def test_env_root_overrides_default_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "config").mkdir(parents=True, exist_ok=True)
            (root / "config" / "luxar.yaml").write_text(
                "agent:\n"
                "  workspace: ./workspace/projects\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LUXAR_ROOT": str(root)}, clear=False):
                manager = ConfigManager()

            self.assertEqual(root.resolve(), manager.project_root())
            self.assertEqual((root / "workspace" / "projects").resolve(), manager.workspace_root())

    def test_env_config_overrides_default_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            custom_config = root / "custom" / "luxar.yaml"
            custom_config.parent.mkdir(parents=True, exist_ok=True)
            custom_config.write_text(
                "agent:\n"
                "  workspace: ./workspace/projects\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"LUXAR_CONFIG": str(custom_config)}, clear=False):
                manager = ConfigManager()

            self.assertEqual(custom_config.resolve(), manager.config_path.resolve())
            self.assertEqual(custom_config.parent.parent.resolve(), manager.project_root())

    def test_raises_when_root_cannot_be_discovered_without_override(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("luxar.core.config_manager._discover_project_root", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "LUXAR_ROOT"):
                    ConfigManager()

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
                "  skill_library: ./workspace/skill_library\n"
                "  firmware_library: ./workspace/firmware_library\n",
                encoding="utf-8",
            )

            manager = ConfigManager(config_path)

            self.assertEqual((root / "workspace" / "projects").resolve(), manager.workspace_root())
            self.assertEqual((root / "workspace" / "driver_library").resolve(), manager.driver_library_root())
            self.assertEqual((root / "workspace" / "skill_library").resolve(), manager.skill_library_root())
            self.assertEqual((root / "workspace" / "firmware_library").resolve(), manager.firmware_library_root())


if __name__ == "__main__":
    unittest.main()

