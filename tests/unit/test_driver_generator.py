from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.config_manager import AgentConfig
from luxar.core.driver_generator import DriverGenerator
from luxar.core.llm_client import LLMResponse


class DriverGeneratorTests(unittest.TestCase):
    def test_generate_driver_writes_header_and_source_files(self) -> None:
        config = AgentConfig()
        generator = DriverGenerator(config)

        fake_response = LLMResponse(
            provider="test",
            model="test-model",
            content=(
                "```c header\n"
                "#ifndef BMI270_H\n#define BMI270_H\nint bmi270_init(void);\n#endif\n"
                "```\n"
                "```c source\n"
                '#include "bmi270.h"\nint bmi270_init(void)\n{\n    return 0;\n}\n'
                "```"
            ),
            raw={},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(generator.llm_client, "complete", return_value=fake_response):
                result = generator.generate_driver(
                    chip="BMI270",
                    interface="spi",
                    protocol_summary="spi accelerometer",
                    register_summary="reg summary",
                    output_dir=tmpdir,
                )

            self.assertTrue(result.success)
            self.assertTrue(Path(result.header_path).exists())
            self.assertTrue(Path(result.source_path).exists())
            self.assertIn("bmi270_init", Path(result.source_path).read_text(encoding="utf-8"))

    def test_generate_driver_reports_bad_response_shape(self) -> None:
        config = AgentConfig()
        generator = DriverGenerator(config)
        fake_response = LLMResponse(
            provider="test",
            model="test-model",
            content="no fenced code blocks here",
            raw={},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(generator.llm_client, "complete", return_value=fake_response):
                result = generator.generate_driver(
                    chip="BMI270",
                    interface="spi",
                    protocol_summary="spi accelerometer",
                    register_summary="reg summary",
                    output_dir=tmpdir,
                )

            self.assertFalse(result.success)
            self.assertIn("separate header/source code blocks", result.error)

    def test_generate_driver_records_reuse_context(self) -> None:
        config = AgentConfig()
        with tempfile.TemporaryDirectory() as tmpdir:
            generator = DriverGenerator(config, project_root=tmpdir)
            fake_response = LLMResponse(
                provider="test",
                model="test-model",
                content=(
                    "```c header\n"
                    "#ifndef BMI270_H\n#define BMI270_H\nint bmi270_init(void);\n#endif\n"
                    "```\n"
                    "```c source\n"
                    '#include "bmi270.h"\nint bmi270_init(void)\n{\n    return 0;\n}\n'
                    "```"
                ),
                raw={},
            )
            with mock.patch.object(generator.reuse_advisor, "build_context", return_value={"summary": "reuse summary", "sources": ["skill:spi"]}), \
                 mock.patch.object(generator.llm_client, "complete", return_value=fake_response):
                result = generator.generate_driver(
                    chip="BMI270",
                    interface="spi",
                    protocol_summary="spi accelerometer",
                    register_summary="reg summary",
                    output_dir=tmpdir,
                )

            self.assertTrue(result.success)
            self.assertEqual("reuse summary", result.reuse_summary)
            self.assertEqual(["skill:spi"], result.reuse_sources)

    def test_generate_driver_reuses_existing_reviewed_driver(self) -> None:
        config = AgentConfig()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            generator = DriverGenerator(config, project_root=root)
            existing_header = root / "existing.h"
            existing_source = root / "existing.c"
            existing_header.write_text("int bmi270_init(void);\n", encoding="utf-8")
            existing_source.write_text("int bmi270_init(void){return 0;}\n", encoding="utf-8")
            reuse_candidate = {
                "name": "bmi270",
                "protocol": "SPI",
                "chip": "BMI270",
                "vendor": "bosch",
                "device": "bmi270",
                "path": str(existing_source),
                "header_path": str(existing_header),
                "source_path": str(existing_source),
                "review_passed": True,
                "source_doc": "",
                "review_issue_count": 0,
                "stored_at": "2026-04-24T00:00:00",
            }
            with mock.patch.object(
                generator.reuse_advisor,
                "build_context",
                return_value={"summary": "reuse summary", "sources": ["driver:bmi270"], "reuse_candidate": reuse_candidate},
            ), mock.patch.object(generator.llm_client, "complete") as complete_mock:
                result = generator.generate_driver(
                    chip="BMI270",
                    interface="spi",
                    protocol_summary="spi accelerometer",
                    register_summary="reg summary",
                    output_dir=tmpdir,
                    vendor="bosch",
                    device="bmi270",
                )

            self.assertTrue(result.success)
            self.assertTrue(result.reused_existing)
            self.assertEqual(str(existing_source), result.reused_driver_path)
            self.assertTrue(Path(result.header_path).exists())
            self.assertTrue(Path(result.source_path).exists())
            complete_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

