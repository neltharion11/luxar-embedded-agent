from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.config_manager import AgentConfig
from luxar.core.driver_pipeline import DriverPipeline
from luxar.models.schemas import (
    DriverGenerationResult,
    DriverMetadata,
    ReviewIssue,
    ReviewReport,
)


class DriverPipelineTests(unittest.TestCase):
    def test_pipeline_succeeds_when_initial_review_passes(self) -> None:
        config = AgentConfig()

        with tempfile.TemporaryDirectory() as tmpdir:
            header = Path(tmpdir) / "bmi270.h"
            source = Path(tmpdir) / "bmi270.c"
            header.write_text("int bmi270_init(void);\n", encoding="utf-8")
            source.write_text("int bmi270_init(void)\n{\n    return 0;\n}\n", encoding="utf-8")

            generation_result = DriverGenerationResult(
                success=True,
                chip="BMI270",
                interface="SPI",
                output_dir=tmpdir,
                header_path=str(header),
                source_path=str(source),
            )
            passing_report = ReviewReport(
                passed=True,
                total_issues=0,
                critical_count=0,
                error_count=0,
                warning_count=0,
                issues=[],
            )

            pipeline = DriverPipeline(config=config, project_root=tmpdir)
            with mock.patch.object(pipeline.generator, "generate_driver", return_value=generation_result), \
                 mock.patch("luxar.core.driver_pipeline.ReviewEngine.review_files", return_value=passing_report), \
                 mock.patch.object(pipeline.driver_library, "store_driver") as store_driver_mock:
                store_driver_mock.side_effect = lambda metadata: DriverMetadata.model_validate(
                    metadata.model_dump(mode="json")
                )
                result = pipeline.generate_review_fix(
                    chip="BMI270",
                    interface="SPI",
                    protocol_summary="summary",
                )

            self.assertTrue(result.success)
            self.assertEqual(0, result.fix_iterations)
            self.assertTrue(result.stored)
            self.assertEqual(1, len(result.stored_records))
            store_driver_mock.assert_called_once()

    def test_pipeline_attempts_fix_until_review_passes(self) -> None:
        config = AgentConfig()

        with tempfile.TemporaryDirectory() as tmpdir:
            header = Path(tmpdir) / "bmi270.h"
            source = Path(tmpdir) / "bmi270.c"
            header.write_text("int bmi270_init(void);\n", encoding="utf-8")
            source.write_text("int bmi270_init(void)\n{\n    return 0;\n}\n", encoding="utf-8")

            generation_result = DriverGenerationResult(
                success=True,
                chip="BMI270",
                interface="SPI",
                output_dir=tmpdir,
                header_path=str(header),
                source_path=str(source),
            )
            failing_report = ReviewReport(
                passed=False,
                total_issues=1,
                critical_count=0,
                error_count=1,
                warning_count=0,
                issues=[
                    ReviewIssue(
                        file=str(source),
                        line=1,
                        severity="error",
                        rule_id="EMB-005",
                        message="Pointer parameter is not validated before use.",
                        suggestion="Add a null check.",
                    )
                ],
            )
            passing_report = ReviewReport(
                passed=True,
                total_issues=0,
                critical_count=0,
                error_count=0,
                warning_count=0,
                issues=[],
            )

            pipeline = DriverPipeline(config=config, project_root=tmpdir)
            with mock.patch.object(pipeline.generator, "generate_driver", return_value=generation_result), \
                 mock.patch(
                     "luxar.core.driver_pipeline.ReviewEngine.review_files",
                     side_effect=[failing_report, passing_report],
                 ), \
                 mock.patch.object(pipeline.fixer, "fix_file") as fix_file_mock, \
                 mock.patch.object(pipeline.driver_library, "store_driver") as store_driver_mock:
                store_driver_mock.side_effect = lambda metadata: DriverMetadata.model_validate(
                    metadata.model_dump(mode="json")
                )
                fix_file_mock.return_value = mock.Mock(success=True)
                result = pipeline.generate_review_fix(
                    chip="BMI270",
                    interface="SPI",
                    protocol_summary="summary",
                    max_fix_iterations=2,
                )

            self.assertTrue(result.success)
            self.assertEqual(1, result.fix_iterations)
            self.assertEqual([str(source)], result.fixed_files)
            self.assertTrue(result.stored)
            fix_file_mock.assert_called_once()
            store_driver_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()

