from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.config_manager import AgentConfig
from luxar.models.schemas import BuildResult
from luxar.tools.build_project import run_build_project


class BuildReviewGateTests(unittest.TestCase):
    def test_build_is_blocked_when_review_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            config = AgentConfig()

            with mock.patch(
                "luxar.tools.build_project.ReviewEngine.review_project",
            ) as review_project_mock:
                review_project_mock.return_value = mock.Mock(
                    passed=False,
                    issues=[
                        mock.Mock(
                            severity="error",
                            rule_id="EMB-006",
                            file=str(project_dir / "bad.c"),
                            line=5,
                            message="Hardcoded peripheral register address detected.",
                        ),
                        mock.Mock(
                            severity="warning",
                            rule_id="EMB-003",
                            file=str(project_dir / "bad.c"),
                            line=1,
                            message="Exported function is missing a Doxygen-style comment.",
                        ),
                    ],
                )

                result = run_build_project(
                    project_path=str(project_dir),
                    config=config,
                    project_root=str(project_dir),
                    clean=False,
                    skip_review=False,
                )

            self.assertIsInstance(result, BuildResult)
            self.assertFalse(result.success)
            self.assertEqual(-2, result.return_code)
            self.assertIn("EMB-006", result.errors[0])

    def test_build_skip_review_bypasses_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            config = AgentConfig()
            expected = BuildResult(success=True, return_code=0)

            with mock.patch(
                "luxar.tools.build_project.BuildSystem.build_project",
                return_value=expected,
            ) as build_project_mock:
                result = run_build_project(
                    project_path=str(project_dir),
                    config=config,
                    project_root=str(project_dir),
                    clean=False,
                    skip_review=True,
                )

            self.assertTrue(result.success)
            build_project_mock.assert_called_once()

    def test_build_auto_fixes_known_app_review_issues_then_builds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            app_file = project_dir / "App" / "Src" / "app_main.c"
            app_file.parent.mkdir(parents=True, exist_ok=True)
            app_file.write_text("void app_main(void) {}\n", encoding="utf-8")
            config = AgentConfig()
            initial_report = mock.Mock(
                issues=[
                    mock.Mock(
                        severity="error",
                        rule_id="EMB-004",
                        file=str(app_file),
                        line=10,
                        message="Driver code must not use printf.",
                    ),
                    mock.Mock(
                        severity="warning",
                        rule_id="EMB-003",
                        file=str(app_file),
                        line=1,
                        message="Exported function is missing a Doxygen-style comment.",
                    ),
                ],
            )
            fixed_report = mock.Mock(issues=[])
            expected = BuildResult(success=True, return_code=0)

            with mock.patch(
                "luxar.tools.build_project.ReviewEngine.review_project",
                side_effect=[initial_report, fixed_report],
            ), \
            mock.patch(
                "luxar.tools.build_project.RepairWorker.repair_file",
                return_value={"success": True, "applied": True, "error": ""},
            ) as fix_mock, \
            mock.patch(
                "luxar.tools.build_project.BuildSystem.build_project",
                return_value=expected,
            ) as build_project_mock:
                result = run_build_project(
                    project_path=str(project_dir),
                    config=config,
                    project_root=str(project_dir),
                    clean=False,
                    skip_review=False,
                )

            self.assertTrue(result.success)
            fix_mock.assert_called_once()
            build_project_mock.assert_called_once()

    def test_build_respects_empty_auto_fix_whitelist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            app_file = project_dir / "App" / "Src" / "app_main.c"
            app_file.parent.mkdir(parents=True, exist_ok=True)
            app_file.write_text("void app_main(void) {}\n", encoding="utf-8")
            config = AgentConfig()
            config.review.auto_fix_rule_ids = []
            review_report = mock.Mock(
                issues=[
                    mock.Mock(
                        severity="error",
                        rule_id="EMB-004",
                        file=str(app_file),
                        line=10,
                        message="Driver code must not use printf.",
                    ),
                ],
            )

            with mock.patch(
                "luxar.tools.build_project.ReviewEngine.review_project",
                return_value=review_report,
            ), \
            mock.patch("luxar.tools.build_project.RepairWorker.repair_file") as fix_mock:
                result = run_build_project(
                    project_path=str(project_dir),
                    config=config,
                    project_root=str(project_dir),
                    clean=False,
                    skip_review=False,
                )

            self.assertFalse(result.success)
            fix_mock.assert_not_called()

    def test_build_respects_auto_fix_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            app_file = project_dir / "App" / "Src" / "app_main.c"
            app_file.parent.mkdir(parents=True, exist_ok=True)
            app_file.write_text("void app_main(void) {}\n", encoding="utf-8")
            config = AgentConfig()
            config.review.auto_fix_enabled = False
            review_report = mock.Mock(
                issues=[
                    mock.Mock(
                        severity="error",
                        rule_id="EMB-004",
                        file=str(app_file),
                        line=10,
                        message="Driver code must not use printf.",
                    ),
                ],
            )

            with mock.patch(
                "luxar.tools.build_project.ReviewEngine.review_project",
                return_value=review_report,
            ), \
            mock.patch("luxar.tools.build_project.RepairWorker.repair_file") as fix_mock:
                result = run_build_project(
                    project_path=str(project_dir),
                    config=config,
                    project_root=str(project_dir),
                    clean=False,
                    skip_review=False,
                )

            self.assertFalse(result.success)
            fix_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()


