from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.skill_manager import SkillManager


class SkillManagerTests(unittest.TestCase):
    def test_update_protocol_skill_writes_skill_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SkillManager(config=AgentConfig(), project_root=tmpdir)
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
            skill_text = skill_path.read_text(encoding="utf-8")
            self.assertIn("SPI", skill_text)
            self.assertTrue("适用范围" in skill_text or "Generic Driver" in skill_text)
            self.assertEqual(["DirectF1C"], artifact.source_projects)

    def test_should_update_protocol_skill_honors_require_project_success(self) -> None:
        config = AgentConfig()
        manager = SkillManager(config=config, project_root=".")
        self.assertTrue(manager.should_update_protocol_skill(True, True, True))
        self.assertFalse(manager.should_update_protocol_skill(True, True, False))

    def test_list_skills_returns_all_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SkillManager(config=AgentConfig(), project_root=tmpdir)
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


if __name__ == "__main__":
    unittest.main()

