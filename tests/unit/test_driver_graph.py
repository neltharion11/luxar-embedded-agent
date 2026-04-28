from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.config_manager import AgentConfig
from luxar.core.driver_pipeline import DriverPipeline
from luxar.models.schemas import DriverGenerationResult, DriverPipelineResult
from luxar.workflows.driver_graph import LangGraphDriverWorkflow


class DriverGraphTests(unittest.TestCase):
    def test_falls_back_to_pipeline_when_langgraph_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = DriverPipeline(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDriverWorkflow(pipeline)
            expected = DriverPipelineResult(
                success=True,
                chip="BMI270",
                interface="SPI",
                generated_files=[str(Path(tmpdir) / "bmi270.c")],
                generation_result=DriverGenerationResult(
                    success=True,
                    chip="BMI270",
                    interface="SPI",
                    output_dir=tmpdir,
                    header_path=str(Path(tmpdir) / "bmi270.h"),
                    source_path=str(Path(tmpdir) / "bmi270.c"),
                ),
                stored=True,
            )

            with mock.patch("luxar.workflows.driver_graph.LANGGRAPH_AVAILABLE", False), mock.patch.object(
                pipeline,
                "generate_review_fix",
                return_value=expected,
            ) as generate_review_fix_mock:
                result = workflow.run(
                    chip="BMI270",
                    interface="SPI",
                    protocol_summary="summary",
                )

            self.assertTrue(result.success)
            generate_review_fix_mock.assert_called_once()

    def test_reused_generation_result_can_flow_through_workflow_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = DriverPipeline(config=AgentConfig(), project_root=tmpdir)
            workflow = LangGraphDriverWorkflow(pipeline)
            expected = DriverPipelineResult(
                success=True,
                chip="BMI270",
                interface="SPI",
                generated_files=[str(Path(tmpdir) / "bmi270.c")],
                generation_result=DriverGenerationResult(
                    success=True,
                    chip="BMI270",
                    interface="SPI",
                    output_dir=tmpdir,
                    header_path=str(Path(tmpdir) / "bmi270.h"),
                    source_path=str(Path(tmpdir) / "bmi270.c"),
                    reused_existing=True,
                    reused_driver_path=str(Path(tmpdir) / "existing.c"),
                    reuse_summary="reuse summary",
                    reuse_sources=["driver:bmi270"],
                ),
                stored=True,
            )

            with mock.patch("luxar.workflows.driver_graph.LANGGRAPH_AVAILABLE", False), mock.patch.object(
                pipeline,
                "generate_review_fix",
                return_value=expected,
            ):
                result = workflow.run(
                    chip="BMI270",
                    interface="SPI",
                    protocol_summary="summary",
                )

            self.assertTrue(result.generation_result.reused_existing)


if __name__ == "__main__":
    unittest.main()

