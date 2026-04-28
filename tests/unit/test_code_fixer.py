from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.code_fixer import CodeFixer
from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMResponse
from luxar.models.schemas import ReviewIssue, ReviewReport


class CodeFixerTests(unittest.TestCase):
    def test_fix_file_writes_updated_code_when_apply_changes_true(self) -> None:
        config = AgentConfig()
        fixer = CodeFixer(config)

        review_report = ReviewReport(
            passed=False,
            total_issues=1,
            critical_count=0,
            error_count=1,
            warning_count=0,
            issues=[
                ReviewIssue(
                    file="demo.c",
                    line=2,
                    severity="error",
                    rule_id="EMB-005",
                    message="Pointer parameter is not validated before use.",
                    suggestion="Add a null check.",
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "demo.c"
            file_path.write_text("int demo(int *ctx)\n{\n    return *ctx;\n}\n", encoding="utf-8")

            with mock.patch.object(
                fixer.llm_client,
                "complete",
                return_value=LLMResponse(
                    provider="test",
                    model="test-model",
                    content="```c\nint demo(int *ctx)\n{\n    if (!ctx) {\n        return -1;\n    }\n    return *ctx;\n}\n```",
                    raw={},
                ),
            ):
                result = fixer.fix_file(
                    project_path=tmpdir,
                    file_path=str(file_path),
                    review_report=review_report,
                    apply_changes=True,
                )

            self.assertTrue(result.success)
            self.assertTrue(result.applied)
            self.assertIn("if (!ctx)", file_path.read_text(encoding="utf-8"))

    def test_fix_file_dry_run_does_not_modify_source(self) -> None:
        config = AgentConfig()
        fixer = CodeFixer(config)

        review_report = ReviewReport(
            passed=False,
            total_issues=1,
            critical_count=0,
            error_count=1,
            warning_count=0,
            issues=[
                ReviewIssue(
                    file="demo.c",
                    line=2,
                    severity="error",
                    rule_id="EMB-005",
                    message="Pointer parameter is not validated before use.",
                    suggestion="Add a null check.",
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "demo.c"
            original = "int demo(int *ctx)\n{\n    return *ctx;\n}\n"
            file_path.write_text(original, encoding="utf-8")

            with mock.patch.object(
                fixer.llm_client,
                "complete",
                return_value=LLMResponse(
                    provider="test",
                    model="test-model",
                    content="```c\nint demo(int *ctx)\n{\n    if (!ctx) {\n        return -1;\n    }\n    return *ctx;\n}\n```",
                    raw={},
                ),
            ):
                result = fixer.fix_file(
                    project_path=tmpdir,
                    file_path=str(file_path),
                    review_report=review_report,
                    apply_changes=False,
                )

            self.assertTrue(result.success)
            self.assertFalse(result.applied)
            self.assertEqual(original, file_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

