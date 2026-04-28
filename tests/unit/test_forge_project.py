from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from luxar.core.config_manager import AgentConfig
from luxar.models.schemas import (
    AppGenerationResult,
    BuildResult,
    DriverMetadata,
    DriverPipelineResult,
    DriverRequirement,
    FlashResult,
    MonitorResult,
    ProjectConfig,
    ProjectPlan,
    ReviewReport,
    EngineeringContext,
)
from luxar.tools.forge_project import _parse_manual_driver_override, run_forge_project


class ForgeProjectTests(unittest.TestCase):
    def _project(self, root: str) -> ProjectConfig:
        return ProjectConfig(
            name="Demo",
            path=root,
            project_mode="firmware",
            mcu="STM32F103C8T6",
        )

    def _plan(self) -> ProjectPlan:
        return ProjectPlan(
            requirement_summary="Blink LED once per second and read BMI270 over SPI.",
            features=["Blink LED once per second.", "Read BMI270 over SPI."],
            needed_drivers=[
                DriverRequirement(
                    chip="BMI270",
                    interface="SPI",
                    vendor="bosch",
                    device="bmi270",
                    confidence=0.8,
                    rationale="Explicit device mention.",
                )
            ],
            peripheral_hints=["GPIO output required for an LED indicator.", "SPI peripheral is required for BMI270."],
            cubemx_or_firmware_actions=["Configure LED GPIO.", "Configure one SPI peripheral."],
            app_behavior_summary="Periodic blink and periodic sensor reads.",
            risk_notes=["LED pin is not specified."],
            used_fallback=True,
        )

    def _app_result(self, root: str, plan: ProjectPlan) -> AppGenerationResult:
        header_path = Path(root) / "App" / "Inc" / "app_main.h"
        source_path = Path(root) / "App" / "Src" / "app_main.c"
        header_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        header_path.write_text("void app_main_init(void);\n", encoding="utf-8")
        source_path.write_text("void app_main_init(void){}\nvoid app_main_loop(void){}\n", encoding="utf-8")
        return AppGenerationResult(
            success=True,
            project="Demo",
            requirement=plan.requirement_summary,
            project_plan=plan,
            header_path=str(header_path),
            source_path=str(source_path),
        )

    def _review_report(self) -> ReviewReport:
        return ReviewReport(
            passed=True,
            total_issues=0,
            critical_count=0,
            error_count=0,
            warning_count=0,
            issues=[],
        )

    def test_plan_only_does_not_build_flash_or_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = self._plan()
            with patch("luxar.tools.forge_project.ProjectPlanner") as planner_cls, \
                 patch("luxar.tools.forge_project.run_assemble_project") as assemble_mock, \
                 patch("luxar.tools.forge_project.AppGenerator") as app_mock, \
                 patch("luxar.tools.forge_project.run_build_project") as build_mock, \
                 patch("luxar.tools.forge_project.run_flash_project") as flash_mock, \
                 patch("luxar.tools.forge_project.run_monitor_project") as monitor_mock:
                planner_cls.return_value.build_plan.return_value = plan
                result = run_forge_project(
                    config=AgentConfig(),
                    project_root=tmpdir,
                    project=self._project(tmpdir),
                    requirement="Blink LED",
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    plan_only=True,
                )
        self.assertTrue(result.success)
        self.assertEqual("plan", result.steps[0].name)
        assemble_mock.assert_not_called()
        app_mock.assert_not_called()
        build_mock.assert_not_called()
        flash_mock.assert_not_called()
        monitor_mock.assert_not_called()

    def test_reuses_existing_driver_when_candidate_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = self._plan()
            candidate = DriverMetadata(
                name="bmi270",
                protocol="SPI",
                chip="BMI270",
                vendor="bosch",
                device="bmi270",
                path=str(Path(tmpdir) / "driver_library" / "bmi270.c"),
                header_path=str(Path(tmpdir) / "driver_library" / "bmi270.h"),
                source_path=str(Path(tmpdir) / "driver_library" / "bmi270.c"),
                review_passed=True,
            )
            with patch("luxar.tools.forge_project.ProjectPlanner") as planner_cls, \
                 patch("luxar.tools.forge_project.AssetReuseAdvisor") as advisor_cls, \
                 patch("luxar.tools.forge_project.run_assemble_project", return_value={"created_files": [], "installed_drivers": []}), \
                 patch("luxar.tools.forge_project.AppGenerator") as app_cls, \
                 patch("luxar.tools.forge_project.ReviewEngine") as review_cls, \
                 patch("luxar.tools.forge_project.run_build_project", return_value=BuildResult(success=True)):
                planner_cls.return_value.build_plan.return_value = plan
                advisor = advisor_cls.return_value
                advisor.select_reuse_candidate.return_value = candidate
                app_cls.return_value.generate_app.return_value = self._app_result(tmpdir, plan)
                review_cls.return_value.review_files.return_value = self._review_report()
                result = run_forge_project(
                    config=AgentConfig(),
                    project_root=tmpdir,
                    project=self._project(tmpdir),
                    requirement="Read BMI270 over SPI.",
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                )
        self.assertTrue(result.success)
        self.assertTrue(any(step.name == "reuse_drivers" and step.status == "completed" for step in result.steps))
        self.assertTrue(any(step.name == "generate_drivers" and step.status == "skipped" for step in result.steps))

    def test_generates_driver_when_reuse_misses(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = self._plan()
            stored = DriverMetadata(
                name="bmi270",
                protocol="SPI",
                chip="BMI270",
                vendor="bosch",
                device="bmi270",
                path=str(Path(tmpdir) / "driver_library" / "bmi270.c"),
                header_path=str(Path(tmpdir) / "driver_library" / "bmi270.h"),
                source_path=str(Path(tmpdir) / "driver_library" / "bmi270.c"),
                review_passed=True,
            )
            pipeline_result = DriverPipelineResult(
                success=True,
                chip="BMI270",
                interface="SPI",
                generated_files=[],
                stored=True,
                stored_records=[stored],
            )
            with patch("luxar.tools.forge_project.ProjectPlanner") as planner_cls, \
                 patch("luxar.tools.forge_project.AssetReuseAdvisor") as advisor_cls, \
                 patch("luxar.tools.forge_project.DriverPipeline") as pipeline_cls, \
                 patch("luxar.tools.forge_project.run_assemble_project", return_value={"created_files": [], "installed_drivers": []}), \
                 patch("luxar.tools.forge_project.AppGenerator") as app_cls, \
                 patch("luxar.tools.forge_project.ReviewEngine") as review_cls, \
                 patch("luxar.tools.forge_project.run_build_project", return_value=BuildResult(success=True)):
                planner_cls.return_value.build_plan.return_value = plan
                advisor_cls.return_value.select_reuse_candidate.return_value = None
                pipeline_cls.return_value.generate_review_fix.return_value = pipeline_result
                app_cls.return_value.generate_app.return_value = self._app_result(tmpdir, plan)
                review_cls.return_value.review_files.return_value = self._review_report()
                result = run_forge_project(
                    config=AgentConfig(),
                    project_root=tmpdir,
                    project=self._project(tmpdir),
                    requirement="Read BMI270 over SPI.",
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                )
        self.assertTrue(result.success)
        self.assertTrue(any(step.name == "generate_drivers" and step.status == "completed" for step in result.steps))

    def test_missing_probe_and_port_skip_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = ProjectPlan(
                requirement_summary="Blink LED.",
                features=["Blink LED."],
                needed_drivers=[],
                peripheral_hints=["GPIO output required for an LED indicator."],
                cubemx_or_firmware_actions=["Configure LED GPIO."],
                app_behavior_summary="Periodic LED blink.",
                risk_notes=["LED pin is not specified."],
                used_fallback=True,
            )
            with patch("luxar.tools.forge_project.ProjectPlanner") as planner_cls, \
                 patch("luxar.tools.forge_project.run_assemble_project", return_value={"created_files": [], "installed_drivers": []}), \
                 patch("luxar.tools.forge_project.AppGenerator") as app_cls, \
                 patch("luxar.tools.forge_project.ReviewEngine") as review_cls, \
                 patch("luxar.tools.forge_project.run_build_project", return_value=BuildResult(success=True)), \
                 patch("luxar.tools.forge_project.run_flash_project") as flash_mock, \
                 patch("luxar.tools.forge_project.run_monitor_project") as monitor_mock:
                planner_cls.return_value.build_plan.return_value = plan
                app_cls.return_value.generate_app.return_value = self._app_result(tmpdir, plan)
                review_cls.return_value.review_files.return_value = self._review_report()
                result = run_forge_project(
                    config=AgentConfig(),
                    project_root=tmpdir,
                    project=self._project(tmpdir),
                    requirement="Blink LED.",
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                )
        self.assertTrue(result.success)
        flash_mock.assert_not_called()
        monitor_mock.assert_not_called()
        self.assertTrue(any(step.name == "flash" and step.status == "skipped" for step in result.steps))
        self.assertTrue(any(step.name == "monitor" and step.status == "skipped" for step in result.steps))

    def test_manual_driver_override_parses_protocol_and_vendor(self) -> None:
        parsed = _parse_manual_driver_override("bosch/BMI270@spi")
        self.assertEqual("bosch", parsed.vendor)
        self.assertEqual("BMI270", parsed.chip)
        self.assertEqual("SPI", parsed.interface)
        self.assertEqual("bmi270", parsed.device)

        parsed_alt = _parse_manual_driver_override("i2c:SHT31")
        self.assertEqual("I2C", parsed_alt.interface)
        self.assertEqual("SHT31", parsed_alt.chip)

    def test_forge_parses_docs_before_planning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plan = ProjectPlan(
                requirement_summary="Read BMI270 over SPI.",
                features=["Read BMI270 over SPI."],
                needed_drivers=[],
                peripheral_hints=["SPI peripheral is required for BMI270."],
                cubemx_or_firmware_actions=["Configure one SPI peripheral."],
                app_behavior_summary="Initialize BMI270 and poll it in the loop.",
                document_context_summary="BMI270 datasheet summary.",
                risk_notes=["SPI pins are not specified."],
                used_fallback=True,
            )
            engineering = EngineeringContext(
                source_documents=[str(Path(tmpdir) / "bmi270.txt")],
                document_summary="BMI270 datasheet summary.",
                integration_notes=["Configure one SPI peripheral."],
            )
            with patch("luxar.tools.forge_project.DocumentEngineeringAnalyzer") as analyzer_cls, \
                 patch("luxar.tools.forge_project.ProjectPlanner") as planner_cls:
                analyzer_cls.return_value.analyze.return_value = engineering
                planner_cls.return_value.build_plan.return_value = plan
                result = run_forge_project(
                    config=AgentConfig(),
                    project_root=tmpdir,
                    project=self._project(tmpdir),
                    requirement="Read BMI270 over SPI.",
                    driver_library_root=str(Path(tmpdir) / "driver_library"),
                    docs=[str(Path(tmpdir) / "bmi270.txt")],
                    plan_only=True,
                )
        self.assertTrue(result.success)
        self.assertEqual("parse_docs", result.steps[0].name)
        self.assertIn("document_context", result.output)


if __name__ == "__main__":
    unittest.main()
