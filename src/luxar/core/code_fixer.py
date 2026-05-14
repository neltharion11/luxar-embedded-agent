from __future__ import annotations

import json
import re
from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient, LLMClientError
from luxar.core.review_engine import ReviewEngine
from luxar.models.schemas import CodeFixResult, ReviewIssue, ReviewReport
from luxar.prompts.fix_code import FIX_CODE_PROMPT, FIX_CODE_SYSTEM_PROMPT


def parse_build_error_lines(build_stderr: str, stdout: str = "") -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    combined = (build_stderr or "") + "\n" + (stdout or "")
    pattern = re.compile(
        r"^(?P<file>[^:]+):(?P<line>\d+):(?P<column>\d+):\s+(?:fatal )?(?P<severity>error|warning):\s+(?P<message>.+)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(combined):
        issues.append(ReviewIssue(
            file=match.group("file"),
            line=int(match.group("line")),
            column=int(match.group("column")),
            severity="error" if match.group("severity") == "error" else "warning",
            rule_id="BUILD",
            message=match.group("message").strip(),
            suggestion="Fix the compilation error so the file compiles successfully.",
        ))
    return issues


class CodeFixer:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm_client = LLMClient(config)
        self._configure_fast_fix_client()

    def _configure_fast_fix_client(self) -> None:
        review_cfg = self.config.review
        self.llm_client.timeout_sec = max(1, min(self.llm_client.timeout_sec, int(review_cfg.fix_timeout_sec)))
        self.llm_client.retry_attempts = max(1, min(self.llm_client.retry_attempts, int(review_cfg.fix_retry_attempts)))
        self.llm_client.max_tokens = max(256, min(self.llm_client.max_tokens, int(review_cfg.fix_max_tokens)))
        self.llm_client.thinking_enabled = bool(review_cfg.fix_thinking_enabled)
        if not self.llm_client.thinking_enabled:
            self.llm_client.thinking_effort = "medium"

    def fix_file(
        self,
        project_path: str,
        file_path: str,
        review_report: ReviewReport | None = None,
        build_errors: list[str] | None = None,
        apply_changes: bool = True,
    ) -> CodeFixResult:
        project_root = Path(project_path).resolve()
        target = Path(file_path)
        if not target.is_absolute():
            target = project_root / target
        target = target.resolve()

        if not target.exists():
            return CodeFixResult(
                success=False,
                file_path=str(target),
                applied=False,
                raw_response="",
                review_report=review_report,
                error=f"Target file not found: {target}",
            )

        source = target.read_text(encoding="utf-8")
        report = review_report or self._deterministic_review_file(project_root, target)

        if build_errors:
            report = self._enrich_report_with_build_errors(report, build_errors, str(target))

        if report.passed:
            return CodeFixResult(
                success=True,
                file_path=str(target),
                applied=False,
                raw_response=source,
                review_report=report,
            )

        prompt = self._build_fix_prompt(target=target, source=source, report=report)
        try:
            response = self.llm_client.complete(
                prompt=prompt,
                system_prompt=FIX_CODE_SYSTEM_PROMPT,
            )
        except (LLMClientError, Exception) as exc:
            return CodeFixResult(
                success=False,
                file_path=str(target),
                applied=False,
                raw_response="",
                review_report=report,
                error=str(exc)[:500],
            )
        try:
            fixed_code = self._extract_fixed_code(target=target, content=response.content)
        except ValueError as exc:
            return CodeFixResult(
                success=False,
                file_path=str(target),
                applied=False,
                raw_response=response.content,
                review_report=report,
                error=str(exc),
            )

        # VERIFICATION GATE: re-review to confirm fix resolved the issues
        if apply_changes:
            re_review = self._verify_fixed_code(
                project_root=project_root,
                target=target,
                fixed_code=fixed_code,
                original_report=report,
                build_errors=build_errors or [],
            )
            if re_review is not None and (re_review.critical_count > 0 or re_review.error_count > 0):
                return CodeFixResult(
                    success=False,
                    file_path=str(target),
                    applied=False,
                    raw_response=response.content,
                    review_report=re_review,
                    error=self._summarize_verification_failure(re_review),
                )

        if apply_changes:
            target.write_text(fixed_code.rstrip() + "\n", encoding="utf-8")

        return CodeFixResult(
            success=True,
            file_path=str(target),
            applied=apply_changes,
            raw_response=response.content,
            review_report=report,
        )

    def fix_files(
        self,
        project_path: str,
        build_result_stderr: str = "",
        build_result_stdout: str = "",
        apply_changes: bool = True,
    ) -> list[CodeFixResult]:
        project_root = Path(project_path).resolve()
        all_build_issues = parse_build_error_lines(build_result_stderr, build_result_stdout)
        file_to_issues: dict[str, list[str]] = {}
        for issue in all_build_issues:
            resolved = (project_root / issue.file).resolve()
            key = str(resolved)
            file_to_issues.setdefault(key, []).append(issue.message)

        target_files = sorted(file_to_issues.keys())
        if not target_files:
            return []

        results: list[CodeFixResult] = []
        for file_path in target_files:
            errors = file_to_issues.get(file_path, [])
            result = self.fix_file(
                project_path=str(project_root),
                file_path=file_path,
                build_errors=errors,
                apply_changes=apply_changes,
            )
            results.append(result)
        return results

    def _deterministic_review_file(self, project_root: Path, target: Path, code: str | None = None) -> ReviewReport:
        engine = ReviewEngine(str(project_root))
        engine.config.review.layers.semantic_review = False
        return engine.review_file(str(target), code=code)

    def _build_fix_prompt(self, target: Path, source: str, report: ReviewReport) -> str:
        report_text = self._render_review_report(report)
        if target.name == "CMakeLists.txt":
            return (
                "请根据审查报告修复以下 CMake 配置。\n\n"
                "【原始代码】\n"
                "```cmake\n"
                f"{source}\n"
                "```\n\n"
                "【审查报告】\n"
                f"{report_text}\n\n"
                "【修复要求】\n"
                "1. 输出完整修复后的 CMakeLists.txt，不要解释\n"
                "2. 只修复与当前 BUILD/审查问题直接相关的配置\n"
                "3. 保持现有 target 名称、目录结构和大部分格式不变\n"
                "4. 如果错误要求补充源码、头文件或 target_sources/list 条目，只添加必要的一行或最小改动\n"
                "5. 不要输出自然语言说明，不要写 TODO 列表，只输出完整文件内容"
            )
        return FIX_CODE_PROMPT.format(
            code=source,
            review_report=report_text,
        )

    def _should_verify_with_review(self, target: Path) -> bool:
        if target.name == "CMakeLists.txt":
            return False
        return target.suffix.lower() in {".c", ".h"}

    def _verify_fixed_code(
        self,
        *,
        project_root: Path,
        target: Path,
        fixed_code: str,
        original_report: ReviewReport,
        build_errors: list[str],
    ) -> ReviewReport | None:
        if not self._should_verify_with_review(target):
            return None

        re_review = self._deterministic_review_file(project_root, target, code=fixed_code.rstrip() + "\n")

        # Old compiler diagnostics are stale after the file changes. Keep verification
        # focused on issues that still reproduce from the new content itself.
        original_blocking_rule_ids = {
            issue.rule_id
            for issue in original_report.issues
            if issue.rule_id != "BUILD" and issue.severity in {"critical", "error"}
        }
        blocking_non_build = [
            issue for issue in re_review.issues
            if issue.rule_id != "BUILD"
            and issue.severity in {"critical", "error"}
            and issue.rule_id in original_blocking_rule_ids
        ]
        if blocking_non_build:
            critical = sum(1 for issue in re_review.issues if issue.severity == "critical")
            errors = sum(1 for issue in re_review.issues if issue.severity == "error")
            warnings = sum(1 for issue in re_review.issues if issue.severity == "warning")
            return ReviewReport(
                passed=False,
                total_issues=len(re_review.issues),
                critical_count=critical,
                error_count=errors,
                warning_count=warnings,
                issues=re_review.issues,
                raw_logs=dict(re_review.raw_logs),
            )

        had_non_build_blockers = bool(original_blocking_rule_ids)
        if had_non_build_blockers:
            return ReviewReport(
                passed=True,
                total_issues=len([issue for issue in re_review.issues if issue.severity == "warning"]),
                critical_count=0,
                error_count=0,
                warning_count=sum(1 for issue in re_review.issues if issue.severity == "warning"),
                issues=[issue for issue in re_review.issues if issue.severity == "warning"],
                raw_logs=dict(re_review.raw_logs),
            )

        return None

    def _enrich_report_with_build_errors(
        self,
        report: ReviewReport,
        build_error_strings: list[str],
        target_path: str,
    ) -> ReviewReport:
        if not build_error_strings:
            return report
        build_issues = _compile_build_error_issues(target_path, build_error_strings)
        if not build_issues:
            return report
        merged_issues = list(report.issues) + build_issues
        critical = sum(1 for i in merged_issues if i.severity == "critical")
        errors = sum(1 for i in merged_issues if i.severity == "error")
        warnings = sum(1 for i in merged_issues if i.severity == "warning")
        return ReviewReport(
            passed=(critical == 0 and errors == 0),
            total_issues=len(merged_issues),
            critical_count=critical,
            error_count=errors,
            warning_count=warnings,
            issues=merged_issues,
            raw_logs=dict(report.raw_logs),
        )

    def _render_review_report(self, report: ReviewReport) -> str:
        return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)

    def _extract_fixed_code(self, *, target: Path, content: str) -> str:
        match = re.search(r"```[^\n]*\n(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if not content.strip():
            raise ValueError("LLM response did not contain fixed code.")
        if self._requires_fenced_fix_output(target):
            raise ValueError(f"LLM response for {target.name} did not contain a fenced code block.")
        return content.strip()

    def _requires_fenced_fix_output(self, target: Path) -> bool:
        if target.name == "CMakeLists.txt":
            return True
        return target.suffix.lower() in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx", ".s", ".ld"}

    def _summarize_verification_failure(self, report: ReviewReport) -> str:
        blocking = [issue for issue in report.issues if issue.severity in {"critical", "error"}]
        if not blocking:
            return "Automatic fix verification did not pass."
        snippets = [
            f"{issue.rule_id}@{Path(issue.file).name}:{issue.line} {issue.message}"
            for issue in blocking[:3]
        ]
        return "Automatic fix verification failed: " + " | ".join(snippets)


def _compile_build_error_issues(target_path: str, error_strings: list[str]) -> list[ReviewIssue]:
    basename = Path(target_path).name
    issues: list[ReviewIssue] = []
    gcc_pattern = re.compile(
        r"^(?P<file>[^:]+):(?P<line>\d+):(?P<column>\d+):\s+(?:fatal )?(?P<severity>error|warning):\s+(?P<message>.+)$",
        re.MULTILINE,
    )
    for msg in error_strings:
        matched = False
        for match in gcc_pattern.finditer(msg):
            fname = Path(match.group("file")).name
            if fname != basename:
                continue
            issues.append(ReviewIssue(
                file=target_path,
                line=int(match.group("line")),
                column=int(match.group("column")),
                severity="error" if match.group("severity") == "error" else "warning",
                rule_id="BUILD",
                message=match.group("message").strip(),
                suggestion="Fix the compilation error so the file compiles successfully.",
            ))
            matched = True
        if not matched and msg.strip():
            issues.append(ReviewIssue(
                file=target_path,
                line=1,
                column=0,
                severity="error",
                rule_id="BUILD",
                message=msg.strip()[:300],
                suggestion="Fix this compilation error.",
            ))
    return issues
