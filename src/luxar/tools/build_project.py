from __future__ import annotations

from pathlib import Path

from luxar.core.build_system import BuildSystem, parse_build_error_lines
from luxar.core.config_manager import AgentConfig
from luxar.core.review_engine import ReviewEngine
from luxar.core.toolchain_manager import ToolchainManager
from luxar.models.schemas import BuildResult, ReviewIssue, ReviewReport
from luxar.platforms.stm32_adapter import STM32CubeMXAdapter
from luxar.agent.workers.repair import RepairWorker
import json


def run_build_project(
    project_path: str,
    config: AgentConfig,
    project_root: str,
    clean: bool = False,
    skip_review: bool = False,
):
    if config.review.enabled and not skip_review:
        review_engine = ReviewEngine(project_path)
        review_report = review_engine.review_project()
        actionable_errors = _actionable_review_issues(review_report)
        auto_fix_rule_ids = _auto_fix_rule_ids(config)
        if actionable_errors and _should_auto_fix_review_issues(actionable_errors, auto_fix_rule_ids):
            auto_fix_result = _auto_fix_review_issues(
                project_path=project_path,
                config=config,
                review_report=review_report,
                auto_fix_rule_ids=auto_fix_rule_ids,
            )
            if auto_fix_result is not None:
                return auto_fix_result
            actionable_errors = []

        if actionable_errors:
            return _build_review_failed_result(review_report, actionable_errors)

    toolchain_manager = ToolchainManager(config=config, project_root=project_root)
    system = BuildSystem(
        STM32CubeMXAdapter(
            toolchain_manager=toolchain_manager,
            openocd_interface=config.flash.openocd_interface,
            openocd_target=config.flash.openocd_target,
        )
    )
    build_result = system.build_project(project_path, clean=clean)

    if not build_result.success:
        build_result = _attach_structured_errors(build_result)

    return build_result


def parse_build_errors(build_result: BuildResult) -> list[ReviewIssue]:
    return parse_build_error_lines(
        build_stderr=getattr(build_result, "stderr", "") or "",
        stdout=getattr(build_result, "stdout", "") or "",
    )


def _attach_structured_errors(build_result: BuildResult) -> BuildResult:
    issues = parse_build_error_lines(
        build_stderr=getattr(build_result, "stderr", "") or "",
        stdout=getattr(build_result, "stdout", "") or "",
    )
    if issues:
        file_map: dict[str, list[str]] = {}
        for issue in issues:
            fname = Path(issue.file).name
            file_map.setdefault(fname, []).append(f"{issue.rule_id}:{issue.message}")
        structured = [f"{fname}: " + "; ".join(msgs) for fname, msgs in file_map.items()]
        if not build_result.errors:
            build_result.errors = []
        build_result.errors = list(build_result.errors) + structured
    return build_result


def _actionable_review_issues(review_report) -> list:
    return [
        issue for issue in review_report.issues
        if issue.severity in {"critical", "error"} and "core" not in Path(issue.file).parts
    ]


def _auto_fix_rule_ids(config: AgentConfig) -> set[str]:
    if not config.review.auto_fix_enabled:
        return set()
    return {rule_id.strip().upper() for rule_id in config.review.auto_fix_rule_ids if rule_id.strip()}


def _should_auto_fix_review_issues(actionable_errors: list, auto_fix_rule_ids: set[str]) -> bool:
    if not actionable_errors:
        return False
    for issue in actionable_errors:
        parts = {part.lower() for part in Path(issue.file).parts}
        if "app" not in parts:
            return False
        if issue.rule_id.upper() not in auto_fix_rule_ids:
            return False
    return True


def _auto_fix_review_issues(
    project_path: str,
    config: AgentConfig,
    review_report,
    auto_fix_rule_ids: set[str],
) -> BuildResult | None:
    target_files = []
    for issue in review_report.issues:
        parts = {part.lower() for part in Path(issue.file).parts}
        if "app" in parts and issue.rule_id.upper() in auto_fix_rule_ids:
            target_files.append(str(Path(issue.file)))
    target_files = sorted(set(target_files))
    if not target_files:
        return None

    fix_failures: list[str] = []
    applied_files: list[str] = []
    for file_path in target_files:
        scoped_issues = [
            _issue_to_dict(issue)
            for issue in review_report.issues
            if str(Path(issue.file)) == file_path
        ]
        worker = RepairWorker(config)
        report_json = json.dumps({
            "passed": False,
            "total_issues": len(scoped_issues),
            "critical_count": sum(1 for issue in scoped_issues if issue["severity"] == "critical"),
            "error_count": sum(1 for issue in scoped_issues if issue["severity"] == "error"),
            "warning_count": sum(1 for issue in scoped_issues if issue["severity"] == "warning"),
            "issues": scoped_issues,
            "raw_logs": _raw_logs_dict(getattr(review_report, "raw_logs", {})),
        }, ensure_ascii=False)
        result = worker.repair_file(
            file_path=file_path,
            context_report=report_json,
            skill_instructions="Only fix the issues listed in the report. Provide complete source file.",
            apply_changes=True,
        )
        if not result.get("success"):
            fix_failures.append(f"{Path(file_path).name}: {result.get('error') or 'auto-fix failed verification'}")
            continue
        if result.get("applied"):
            applied_files.append(str(Path(file_path)))

    re_review = ReviewEngine(project_path).review_project()
    remaining_errors = _actionable_review_issues(re_review)
    if fix_failures or remaining_errors:
        build_result = _build_review_failed_result(re_review, remaining_errors)
        build_result.stderr = (
            "Pre-build review failed after automatic App/ fixes."
            if not fix_failures
            else "Automatic App/ review fix did not fully resolve the blocking issues."
        )
        build_result.warnings.extend([f"auto_fix_applied:{Path(path).name}" for path in applied_files])
        build_result.warnings.extend(fix_failures)
        return build_result

    return None


def _issue_to_dict(issue) -> dict:
    model_dump = getattr(issue, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    if isinstance(issue, dict):
        return dict(issue)
    return {
        "file": str(getattr(issue, "file", "")),
        "line": _safe_int(getattr(issue, "line", 1), default=1),
        "column": _safe_int(getattr(issue, "column", 0), default=0),
        "severity": str(getattr(issue, "severity", "error") or "error"),
        "rule_id": str(getattr(issue, "rule_id", "") or ""),
        "message": str(getattr(issue, "message", "") or ""),
        "suggestion": str(getattr(issue, "suggestion", "") or ""),
    }


def _raw_logs_dict(raw_logs) -> dict:
    return raw_logs if isinstance(raw_logs, dict) else {}


def _safe_int(value, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _build_review_failed_result(review_report, actionable_errors: list) -> BuildResult:
    core_info_issues = [
        issue for issue in review_report.issues
        if "core" in Path(issue.file).parts and issue.severity == "info"
    ]
    issue_summaries = [
        f"{issue.rule_id}@{Path(issue.file).name}:{issue.line} {issue.message}"
        for issue in actionable_errors
    ]
    core_notes = [
        f"[CubeMX generated, ignore] {issue.rule_id}@{Path(issue.file).name}:{issue.line}"
        for issue in core_info_issues
    ]
    return BuildResult(
        success=False,
        command=[],
        return_code=-2,
        stdout="",
        stderr="Pre-build review failed. Re-run with --skip-review to bypass the quality gate.",
        errors=issue_summaries or ["review_failed"],
        warnings=[
            f"{issue.rule_id}@{Path(issue.file).name}:{issue.line} {issue.message}"
            for issue in review_report.issues
            if issue.severity == "warning"
        ] + core_notes,
    )
