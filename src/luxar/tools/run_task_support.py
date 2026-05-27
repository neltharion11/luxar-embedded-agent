from __future__ import annotations

import json
import re
from pathlib import Path

from luxar.agent.workers.repair import RepairWorker
from luxar.core.config_manager import AgentConfig
from luxar.core.document_engineering import DocumentEngineeringAnalyzer
from luxar.core.task_router import TaskRouter
from luxar.models.schemas import EngineeringContext, ReviewReport


def prepare_task_execution(
    *,
    task: str,
    project_name: str,
    docs: list[str],
    dry_run: bool,
    plan_only: bool,
    driver_library_root: str,
) -> tuple:
    router = TaskRouter()
    execution_plan = router.route(
        task=task,
        project=project_name,
        docs=docs,
        dry_run=dry_run,
        plan_only=plan_only,
    )
    engineering = build_engineering_context(
        driver_library_root=driver_library_root,
        docs=docs,
        query=task,
    ) if docs else None
    return execution_plan, engineering


def build_engineering_context(*, driver_library_root: str, docs: list[str], query: str):
    analyzer = DocumentEngineeringAnalyzer(Path(driver_library_root).resolve() / "knowledge_base")
    return analyzer.analyze(docs=docs, query=query)


def infer_driver_request(*, task: str, engineering: EngineeringContext | None) -> tuple[str, str]:
    interface = ""
    if engineering and engineering.bus_requirements:
        interface = engineering.bus_requirements[0].interface.upper()
    else:
        lowered = task.lower()
        for candidate in ("spi", "i2c", "uart"):
            if candidate in lowered:
                interface = candidate.upper()
                break

    chip = ""
    if engineering:
        for hint in engineering.register_hints:
            if any(char.isdigit() for char in hint):
                chip = hint
                break
    if not chip:
        match = re.search(r"\b([A-Za-z]{2,}\d[A-Za-z0-9_-]*)\b", task)
        if match:
            chip = match.group(1)
    return chip, interface


def build_explain_message(*, task: str, engineering: EngineeringContext | None) -> str:
    summary = (engineering.document_summary if engineering else "").strip()
    if summary:
        return summary

    lowered = task.strip().lower()
    if any(token in lowered for token in ["你有什么功能", "你能做什么", "功能", "capability", "what can you do", "help"]):
        return "我可以帮你看文档、提取引脚和协议要求、生成或复用驱动、规划 STM32 工程、审查代码、修复一部分编译问题，以及执行 build、flash、monitor、debug 这类流程。你可以直接告诉我目标，比如“审查当前项目代码”或“基于这个 PDF 生成工程”。"
    if lowered in {"hi", "hello", "hey", "你好", "您好", "嗨", "在吗"}:
        return "你好，我在。你可以直接告诉我你想做什么，比如看文档、分析引脚、生成工程、审查代码，或者修复编译问题。"

    if not lowered:
        return "请直接告诉我你的目标，我可以帮你解释文档、规划项目、生成驱动，或者调试当前工程。"

    return f"我理解到你的请求是：{task.strip()}。如果你愿意，我可以继续帮你分析文档、规划工程步骤，或者直接执行对应工作流。"


def should_run_build_only(task: str) -> bool:
    lowered = task.lower()
    build_tokens = ("build", "compile", "rebuild", "编译", "构建", "重建")
    debug_tokens = ("flash", "monitor", "debug", "probe", "port", "串口", "烧录", "调试", "st-link", "stlink")
    return any(token in lowered for token in build_tokens) and not any(token in lowered for token in debug_tokens)


def build_build_message(result: dict) -> str:
    if result.get("success"):
        warnings_count = len(result.get("warnings", []) or [])
        return f"构建已经完成并通过。当前共有 {warnings_count} 条警告。"
    errors = result.get("errors", []) or []
    first_error = errors[0] if errors else result.get("stderr", "构建失败。")
    return f"构建未通过。首个阻塞问题是：{first_error}"


def auto_fix_rule_ids(config: AgentConfig) -> set[str]:
    if not config.review.auto_fix_enabled:
        return set()
    return {rule_id.strip().upper() for rule_id in config.review.auto_fix_rule_ids if rule_id.strip()}


def should_auto_apply_review_fixes(*, task: str, report: dict, auto_fix_rule_ids: set[str]) -> bool:
    lowered = task.lower()
    fix_requested = any(token in lowered for token in ("fix", "修复", "replace", "printf", "doxygen", "注释"))
    if not fix_requested:
        return False
    issues = report.get("issues", []) or []
    blocking = [issue for issue in issues if issue.get("severity") in {"critical", "error"}]
    if not blocking:
        return False
    return all(is_auto_fixable_app_issue(issue, auto_fix_rule_ids) for issue in blocking)


def auto_fix_review_files(
    *,
    config: AgentConfig,
    project_path: str,
    report: dict,
    auto_fix_rule_ids: set[str],
) -> list[str]:
    target_files = sorted({
        issue.get("file", "")
        for issue in report.get("issues", []) or []
        if is_auto_fixable_app_issue(issue, auto_fix_rule_ids)
    })
    fixed_files: list[str] = []
    for file_path in target_files:
        if not file_path:
            continue
        scoped_issues = [
            issue
            for issue in report.get("issues", []) or []
            if issue.get("file", "") == file_path and is_auto_fixable_app_issue(issue, auto_fix_rule_ids)
        ]
        report_json = json.dumps({
            "passed": False,
            "total_issues": len(scoped_issues),
            "critical_count": sum(1 for issue in scoped_issues if issue.get("severity") == "critical"),
            "error_count": sum(1 for issue in scoped_issues if issue.get("severity") == "error"),
            "warning_count": sum(1 for issue in scoped_issues if issue.get("severity") == "warning"),
            "issues": scoped_issues,
            "raw_logs": {},
        }, ensure_ascii=False)
        worker = RepairWorker(config)
        result = worker.repair_file(
            file_path=file_path,
            context_report=report_json,
            skill_instructions="Only fix the issues listed in the report. Provide complete source file.",
            apply_changes=True,
        )
        if result.get("success") and result.get("applied"):
            fixed_files.append(str(Path(file_path).resolve()))
    return fixed_files


def is_auto_fixable_app_issue(issue: dict, auto_fix_rule_ids: set[str]) -> bool:
    file_path = issue.get("file", "")
    parts = {part.lower() for part in Path(file_path).parts}
    rule_id = str(issue.get("rule_id", "")).upper()
    return "app" in parts and rule_id in auto_fix_rule_ids


def build_review_message(report: dict, *, fixed_files: list[str] | None = None) -> str:
    fixed_files = fixed_files or []
    total = int(report.get("total_issues", 0) or 0)
    critical = int(report.get("critical_count", 0) or 0)
    errors = int(report.get("error_count", 0) or 0)
    warnings_count = int(report.get("warning_count", 0) or 0)
    if fixed_files and report.get("passed", False):
        return f"我已经自动修复了 {len(fixed_files)} 个文件，并重新审查通过。当前共有 {warnings_count} 个警告。"
    if fixed_files:
        return (
            f"我已经自动修复了 {len(fixed_files)} 个文件，并重新审查。"
            f"当前仍有 {total} 个问题，其中严重 {critical} 个、错误 {errors} 个、警告 {warnings_count} 个。"
        )
    if report.get("passed", False):
        return f"我已经审查了当前项目代码，结果通过，没有阻塞性问题。当前共有 {warnings_count} 个警告。"
    return f"我已经审查了当前项目代码，结果未通过。当前共有 {total} 个问题，其中严重 {critical} 个、错误 {errors} 个、警告 {warnings_count} 个。"
