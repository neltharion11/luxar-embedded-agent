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
    def test_code_fixer_clamps_llm_settings_for_fast_auto_fix(self) -> None:
        config = AgentConfig()
        config.llm.timeout_sec = 120
        config.llm.retry_attempts = 5
        config.llm.max_tokens = 393216
        config.llm.thinking_enabled = True
        fixer = CodeFixer(config)

        self.assertEqual(30, fixer.llm_client.timeout_sec)
        self.assertEqual(1, fixer.llm_client.retry_attempts)
        self.assertEqual(8192, fixer.llm_client.max_tokens)
        self.assertFalse(fixer.llm_client.thinking_enabled)

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

    def test_fix_file_returns_clear_error_when_target_is_missing(self) -> None:
        config = AgentConfig()
        fixer = CodeFixer(config)

        result = fixer.fix_file(
            project_path="C:/missing/project",
            file_path="C:/missing/project/App/Src/app_main.c",
            apply_changes=True,
        )

        self.assertFalse(result.success)
        self.assertIn("Target file not found:", result.error)

    def test_fix_file_does_not_fail_verification_on_stale_build_errors(self) -> None:
        config = AgentConfig()
        fixer = CodeFixer(config)

        review_report = ReviewReport(
            passed=True,
            total_issues=0,
            critical_count=0,
            error_count=0,
            warning_count=0,
            issues=[],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "CMakeLists.txt"
            file_path.write_text("add_executable(app main.c)\n", encoding="utf-8")

            with mock.patch.object(
                fixer.llm_client,
                "complete",
                return_value=LLMResponse(
                    provider="test",
                    model="test-model",
                    content="```cmake\nadd_executable(app main.c driver.c)\n```",
                    raw={},
                ),
            ):
                result = fixer.fix_file(
                    project_path=tmpdir,
                    file_path=str(file_path),
                    review_report=review_report,
                    build_errors=["Add one line to APP_DRIVER_SOURCES"],
                    apply_changes=True,
                )

            self.assertTrue(result.success)
            self.assertTrue(result.applied)
            self.assertIn("driver.c", file_path.read_text(encoding="utf-8"))

    def test_fix_file_rejects_natural_language_for_c_source(self) -> None:
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
                    file="app_main.c",
                    line=1,
                    severity="error",
                    rule_id="COMPILE",
                    message="unknown type name",
                    suggestion="Return valid C code only.",
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "app_main.c"
            original = "int app_main(void)\n{\n    return 0;\n}\n"
            file_path.write_text(original, encoding="utf-8")

            with mock.patch.object(
                fixer.llm_client,
                "complete",
                return_value=LLMResponse(
                    provider="test",
                    model="test-model",
                    content="Looking at the review report, here's what needs to change.",
                    raw={},
                ),
            ):
                result = fixer.fix_file(
                    project_path=tmpdir,
                    file_path=str(file_path),
                    review_report=review_report,
                    apply_changes=True,
                )

            self.assertFalse(result.success)
            self.assertIn("did not contain a fenced code block", result.error)
            self.assertEqual(original, file_path.read_text(encoding="utf-8"))

    def test_fix_file_uses_cmake_specific_prompt_for_cmakelists(self) -> None:
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
                    file="CMakeLists.txt",
                    line=1,
                    severity="error",
                    rule_id="BUILD",
                    message="Missing target source entry.",
                    suggestion="Add the missing source.",
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "CMakeLists.txt"
            file_path.write_text("add_executable(app main.c)\n", encoding="utf-8")

            with mock.patch.object(
                fixer.llm_client,
                "complete",
                return_value=LLMResponse(
                    provider="test",
                    model="test-model",
                    content="```cmake\nadd_executable(app main.c)\n```",
                    raw={},
                ),
            ) as complete_mock:
                fixer.fix_file(
                    project_path=tmpdir,
                    file_path=str(file_path),
                    review_report=review_report,
                    apply_changes=False,
                )

            prompt = complete_mock.call_args.kwargs["prompt"]
            self.assertIn("```cmake", prompt)
            self.assertIn("完整修复后的 CMakeLists.txt", prompt)


if __name__ == "__main__":
    unittest.main()

