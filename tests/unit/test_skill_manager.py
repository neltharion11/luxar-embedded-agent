from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.config_manager import AgentConfig
from luxar.core.skill_manager import SkillManager


class SkillManagerTests(unittest.TestCase):
    def test_update_protocol_skill_writes_skill_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SkillManager(config=AgentConfig(), project_root=tmpdir)
            with mock.patch.object(
                SkillManager,
                "_generate_skill_markdown",
                return_value="# SPI Protocol Skill\n\nGeneric Driver\n",
            ):
                artifact = manager.update_protocol_skill(
                    protocol="spi",
                    device_name="BMI270",
                    summary="Validated SPI reset and status-register bring-up path.",
                    lessons_learned=["Validate chip-select timing before first read."],
                    platforms=["stm32cubemx"],
                    runtimes=["baremetal"],
                    source_project="DirectF1C",
                )

            skill_path = Path(artifact.path)
            metadata_path = skill_path.parent / "metadata.json"
            self.assertTrue(skill_path.exists())
            self.assertTrue(metadata_path.exists())
            self.assertIn(str(Path(tmpdir) / "workspace" / "skills" / "protocols"), str(skill_path))
            skill_text = skill_path.read_text(encoding="utf-8")
            self.assertIn("SPI", skill_text)
            self.assertTrue("适用范围" in skill_text or "Generic Driver" in skill_text)
            self.assertEqual(["DirectF1C"], artifact.source_projects)
            self.assertFalse((Path(tmpdir) / "workspace" / "skill_library" / "protocols" / "spi" / "SKILL.md").exists())

    def test_should_update_protocol_skill_honors_require_project_success(self) -> None:
        config = AgentConfig()
        manager = SkillManager(config=config, project_root=".")
        self.assertTrue(manager.should_update_protocol_skill(True, True, True))
        self.assertFalse(manager.should_update_protocol_skill(True, True, False))

    def test_list_skills_returns_all_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SkillManager(config=AgentConfig(), project_root=tmpdir)
            with mock.patch.object(
                SkillManager,
                "_generate_skill_markdown",
                return_value="# Protocol Skill\n\nGeneric Driver\n",
            ):
                manager.update_protocol_skill(
                    protocol="spi",
                    device_name="BMI270",
                    summary="SPI skill",
                    lessons_learned=[],
                    platforms=["stm32cubemx"],
                    runtimes=["baremetal"],
                    source_project="ProjA",
                )
                manager.update_protocol_skill(
                    protocol="i2c",
                    device_name="MPU6050",
                    summary="I2C skill",
                    lessons_learned=[],
                    platforms=["stm32cubemx"],
                    runtimes=["freertos"],
                    source_project="ProjB",
                )

            all_skills = manager.list_skills()
            self.assertEqual(len(all_skills), 2)
            protocols = {s["protocol"] for s in all_skills}
            self.assertIn("spi", protocols)
            self.assertIn("i2c", protocols)

    def test_list_skills_filters_by_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SkillManager(config=AgentConfig(), project_root=tmpdir)
            with mock.patch.object(
                SkillManager,
                "_generate_skill_markdown",
                return_value="# Protocol Skill\n\nGeneric Driver\n",
            ):
                manager.update_protocol_skill(
                    protocol="spi", device_name="BMI270", summary="x",
                    lessons_learned=[], platforms=["stm32cubemx"],
                    runtimes=["baremetal"], source_project="ProjA",
                )
                manager.update_protocol_skill(
                    protocol="i2c", device_name="MPU6050", summary="y",
                    lessons_learned=[], platforms=["stm32cubemx"],
                    runtimes=["freertos"], source_project="ProjB",
                )

            filtered = manager.list_skills(protocol="spi")
            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0]["protocol"], "spi")

    def test_list_skills_empty_when_no_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SkillManager(config=AgentConfig(), project_root=tmpdir)
            self.assertEqual(manager.list_skills(), [])

    def test_list_skills_reads_legacy_protocol_skill_as_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_root = Path(tmpdir) / "workspace" / "skill_library" / "protocols" / "spi"
            legacy_root.mkdir(parents=True, exist_ok=True)
            (legacy_root / "SKILL.md").write_text("# SPI Legacy Skill\n", encoding="utf-8")
            (legacy_root / "metadata.json").write_text(
                '{"protocol":"spi","platforms":["stm32cubemx"],"runtimes":["baremetal"],"source_projects":["LegacyProj"],"validation_count":2,"updated_at":"2026-05-18T00:00:00"}',
                encoding="utf-8",
            )

            manager = SkillManager(config=AgentConfig(), project_root=tmpdir)
            skills = manager.list_skills()
            migrated_path = Path(tmpdir) / "workspace" / "skills" / "protocols" / "spi" / "SKILL.md"

            self.assertEqual(1, len(skills))
            self.assertEqual("spi", skills[0]["protocol"])
            self.assertTrue(migrated_path.exists())
            self.assertEqual(str(migrated_path), skills[0]["path"])
            metadata = (Path(tmpdir) / "workspace" / "skills" / "protocols" / "spi" / "metadata.json").read_text(encoding="utf-8")
            self.assertIn("workspace", metadata)
            self.assertIn("skills", metadata)

    def test_update_protocol_skill_imports_legacy_metadata_before_incrementing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_root = Path(tmpdir) / "workspace" / "skill_library" / "protocols" / "spi"
            legacy_root.mkdir(parents=True, exist_ok=True)
            (legacy_root / "SKILL.md").write_text("# SPI Legacy Skill\n", encoding="utf-8")
            (legacy_root / "metadata.json").write_text(
                '{"protocol":"spi","platforms":["stm32cubemx"],"runtimes":["baremetal"],"source_projects":["LegacyProj"],"validation_count":2,"updated_at":"2026-05-18T00:00:00"}',
                encoding="utf-8",
            )

            manager = SkillManager(config=AgentConfig(), project_root=tmpdir)
            with mock.patch.object(
                SkillManager,
                "_generate_skill_markdown",
                return_value="# SPI Protocol Skill\n\nGeneric Driver\n",
            ):
                artifact = manager.update_protocol_skill(
                    protocol="spi",
                    device_name="BMI270",
                    summary="SPI skill",
                    lessons_learned=[],
                    platforms=["stm32cubemx"],
                    runtimes=["baremetal"],
                    source_project="ProjA",
                )

            self.assertEqual(3, artifact.validation_count)
            self.assertEqual(["LegacyProj", "ProjA"], artifact.source_projects)


if __name__ == "__main__":
    unittest.main()

