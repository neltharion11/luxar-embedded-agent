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
                "luxar.tools.build_project.ReviewEngine.discover_project_files",
                return_value=[str(project_dir / "bad.c")],
            ), mock.patch(
                "luxar.tools.build_project.ReviewEngine.review_files",
            ) as review_files_mock:
                review_files_mock.return_value = mock.Mock(
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


if __name__ == "__main__":
    unittest.main()


