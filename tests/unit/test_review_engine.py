from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.llm_client import LLMClientError, LLMResponse
from luxar.core.review_engine import ReviewEngine


def _write_project_metadata(project_dir: Path, project_mode: str = "firmware") -> None:
    metadata = {
        "name": "demo",
        "path": str(project_dir),
        "platform": "stm32cubemx",
        "runtime": "baremetal",
        "project_mode": project_mode,
        "mcu": "STM32F103C8T6",
        "ioc_file": str(project_dir / "demo.ioc"),
        "firmware_package": "",
    }
    (project_dir / ".agent_project.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )


class ReviewEngineTests(unittest.TestCase):
    def test_review_file_reports_expected_custom_rule_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "App" / "Src").mkdir(parents=True)
            (project_dir / "App" / "Inc").mkdir(parents=True)
            _write_project_metadata(project_dir)

            source = project_dir / "App" / "Src" / "sensor.c"
            header = project_dir / "App" / "Inc" / "sensor.h"
            header.write_text("#ifndef SENSOR_H\n#define SENSOR_H\nint sensor_init(void *ctx);\n#endif\n", encoding="utf-8")
            source.write_text(
                '#include "sensor.h"\n'
                "int sensor_init(void *ctx)\n"
                "{\n"
                "    printf(\"bad\\n\");\n"
                "    return *(int *)ctx + *(volatile unsigned int *)0x40021000u;\n"
                "}\n",
                encoding="utf-8",
            )

            engine = ReviewEngine(str(project_dir))
            report = engine.review_file(str(source))

            rule_ids = {issue.rule_id for issue in report.issues}
            self.assertIn("EMB-004", rule_ids)
            self.assertIn("EMB-005", rule_ids)
            self.assertIn("EMB-006", rule_ids)
            self.assertIn("EMB-003", rule_ids)
            self.assertFalse(report.passed)

    def test_review_file_marks_clang_tidy_unavailable_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "App" / "Src").mkdir(parents=True)
            (project_dir / "App" / "Inc").mkdir(parents=True)
            _write_project_metadata(project_dir)

            header = project_dir / "App" / "Inc" / "app_main.h"
            source = project_dir / "App" / "Src" / "app_main.c"
            header.write_text("#ifndef APP_MAIN_H\n#define APP_MAIN_H\nvoid app_main_init(void);\n#endif\n", encoding="utf-8")
            source.write_text(
                "/** init */\nvoid app_main_init(void)\n{\n}\n",
                encoding="utf-8",
            )

            engine = ReviewEngine(str(project_dir))
            with mock.patch("luxar.core.review_engine.shutil.which", return_value=None):
                report = engine.review_file(str(source))

            self.assertIn("clang_tidy", report.raw_logs)
            self.assertFalse(report.raw_logs["clang_tidy"]["enabled"])

    def test_parse_clang_tidy_output_converts_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            _write_project_metadata(project_dir)
            engine = ReviewEngine(str(project_dir))
            report = engine._parse_clang_tidy_output(
                stdout="demo.c:12:4: warning: something happened [bugprone-suspicious]\n",
                stderr="",
                return_code=1,
                file_path=project_dir / "demo.c",
            )

            self.assertEqual(1, report.total_issues)
            self.assertEqual("CLANG-BUGPRONE_SUSPICIOUS", report.issues[0].rule_id)
            self.assertEqual(12, report.issues[0].line)

    def test_cubemx_main_requires_user_code_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "Core" / "Src").mkdir(parents=True)
            _write_project_metadata(project_dir, project_mode="cubemx")

            main_c = project_dir / "Core" / "Src" / "main.c"
            main_c.write_text('#include "app_main.h"\nint main(void)\n{\n    return 0;\n}\n', encoding="utf-8")

            engine = ReviewEngine(str(project_dir))
            report = engine.review_file(str(main_c))

            self.assertIn("EMB-002", {issue.rule_id for issue in report.issues})

    def test_firmware_main_does_not_require_user_code_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "Core" / "Src").mkdir(parents=True)
            _write_project_metadata(project_dir, project_mode="firmware")

            main_c = project_dir / "Core" / "Src" / "main.c"
            main_c.write_text('#include "app_main.h"\nint main(void)\n{\n    return 0;\n}\n', encoding="utf-8")

            engine = ReviewEngine(str(project_dir))
            report = engine.review_file(str(main_c))

            self.assertNotIn("EMB-002", {issue.rule_id for issue in report.issues})

    def test_main_and_system_sources_do_not_require_matching_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "Core" / "Src").mkdir(parents=True)
            _write_project_metadata(project_dir, project_mode="firmware")

            main_c = project_dir / "Core" / "Src" / "main.c"
            system_c = project_dir / "Core" / "Src" / "system_stm32xx.c"
            main_c.write_text("int main(void)\n{\n    return 0;\n}\n", encoding="utf-8")
            system_c.write_text("void SystemInit(void)\n{\n}\n", encoding="utf-8")

            engine = ReviewEngine(str(project_dir))
            main_report = engine.review_file(str(main_c))
            system_report = engine.review_file(str(system_c))

            self.assertNotIn("EMB-010", {issue.rule_id for issue in main_report.issues})
            self.assertNotIn("EMB-010", {issue.rule_id for issue in system_report.issues})

    def test_semantic_review_parses_llm_json_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "App" / "Src").mkdir(parents=True)
            (project_dir / "App" / "Inc").mkdir(parents=True)
            _write_project_metadata(project_dir, project_mode="firmware")

            header = project_dir / "App" / "Inc" / "driver.h"
            source = project_dir / "App" / "Src" / "driver.c"
            header.write_text("int driver_init(int *ctx);\n", encoding="utf-8")
            source.write_text("/** init */\nint driver_init(int *ctx)\n{\n    if (!ctx) { return -1; }\n    return 0;\n}\n", encoding="utf-8")

            engine = ReviewEngine(str(project_dir))
            engine.config.review.layers.static_analysis = False
            engine.config.review.layers.custom_rules = False
            engine.config.review.layers.semantic_review = True

            with mock.patch(
                "luxar.core.review_engine.LLMClient.complete",
                return_value=LLMResponse(
                    provider="test",
                    model="test-model",
                    content='{"passed": false, "issues": [{"severity": "warning", "line": 3, "rule": "logic", "description": "Potential ordering issue", "suggestion": "Check init order"}], "summary": "ok"}',
                    raw={},
                ),
            ):
                report = engine.review_file(str(source))

            self.assertIn("LLM-LOGIC", {issue.rule_id for issue in report.issues})
            self.assertIn("semantic_review", report.raw_logs)
            self.assertTrue(report.raw_logs["semantic_review"]["enabled"])

    def test_semantic_review_missing_api_key_degrades_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "App" / "Src").mkdir(parents=True)
            (project_dir / "App" / "Inc").mkdir(parents=True)
            _write_project_metadata(project_dir, project_mode="firmware")

            header = project_dir / "App" / "Inc" / "driver.h"
            source = project_dir / "App" / "Src" / "driver.c"
            header.write_text("int driver_init(int *ctx);\n", encoding="utf-8")
            source.write_text("/** init */\nint driver_init(int *ctx)\n{\n    if (!ctx) { return -1; }\n    return 0;\n}\n", encoding="utf-8")

            engine = ReviewEngine(str(project_dir))
            engine.config.review.layers.static_analysis = False
            engine.config.review.layers.custom_rules = False
            engine.config.review.layers.semantic_review = True

            with mock.patch(
                "luxar.core.review_engine.LLMClient.complete",
                side_effect=LLMClientError("Missing API key. Set the `DEEPSEEK_API_KEY` environment variable."),
            ):
                report = engine.review_file(str(source))

            self.assertEqual(0, report.total_issues)
            self.assertFalse(report.raw_logs["semantic_review"]["enabled"])

    def test_semantic_review_invalid_json_degrades_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "App" / "Src").mkdir(parents=True)
            (project_dir / "App" / "Inc").mkdir(parents=True)
            _write_project_metadata(project_dir, project_mode="firmware")

            header = project_dir / "App" / "Inc" / "driver.h"
            source = project_dir / "App" / "Src" / "driver.c"
            header.write_text("int driver_init(int *ctx);\n", encoding="utf-8")
            source.write_text("/** init */\nint driver_init(int *ctx)\n{\n    if (!ctx) { return -1; }\n    return 0;\n}\n", encoding="utf-8")

            engine = ReviewEngine(str(project_dir))
            engine.config.review.layers.static_analysis = False
            engine.config.review.layers.custom_rules = False
            engine.config.review.layers.semantic_review = True

            with mock.patch(
                "luxar.core.review_engine.LLMClient.complete",
                return_value=LLMResponse(
                    provider="test",
                    model="test-model",
                    content="not json at all",
                    raw={},
                ),
            ):
                report = engine.review_file(str(source))

            self.assertEqual(0, report.total_issues)
            self.assertFalse(report.raw_logs["semantic_review"]["enabled"])


if __name__ == "__main__":
    unittest.main()


