from __future__ import annotations

from pathlib import Path
import re

from luxar.core.config_manager import AgentConfig
from luxar.core.document_engineering import DocumentEngineeringAnalyzer
from luxar.core.project_manager import ProjectManager
from luxar.core.task_router import TaskRouter
from luxar.models.schemas import EngineeringContext
from luxar.tools.forge_project import run_forge_project
from luxar.tools.generate_driver_loop import run_generate_driver_loop
from luxar.tools.review_code import run_review_project
from luxar.tools.run_workflow import run_debug_workflow


def run_task(
    *,
    config: AgentConfig,
    project_root: str,
    workspace_root: str,
    driver_library_root: str,
    task: str,
    project_name: str = "",
    docs: list[str] | None = None,
    dry_run: bool = False,
    plan_only: bool = False,
    no_build: bool = False,
    no_flash: bool = False,
    no_monitor: bool = False,
) -> dict:
    docs = docs or []
    router = TaskRouter()
    execution_plan = router.route(
        task=task,
        project=project_name,
        docs=docs,
        dry_run=dry_run,
        plan_only=plan_only,
    )
    engineering = _build_engineering_context(
        driver_library_root=driver_library_root,
        docs=docs,
        query=task,
    ) if docs else None

    if execution_plan.missing_info_questions:
        return {
            "success": False,
            "mode": "plan",
            "execution_plan": execution_plan.model_dump(mode="json"),
            "engineering_context": engineering.model_dump(mode="json") if engineering else {},
            "message": execution_plan.missing_info_questions[0],
        }

    intent = execution_plan.intent.intent_type
    if intent == "explain":
        return {
            "success": True,
            "mode": "explain",
            "execution_plan": execution_plan.model_dump(mode="json"),
            "engineering_context": engineering.model_dump(mode="json") if engineering else {},
            "message": _build_explain_message(task=task, engineering=engineering),
        }

    if intent == "project_status":
        project = ProjectManager(workspace_root).load_project(project_name)
        return {
            "success": True,
            "mode": "status",
            "execution_plan": execution_plan.model_dump(mode="json"),
            "project": project.model_dump(mode="json"),
            "message": f"当前项目是 {project.name}，MCU 是 {project.mcu}，平台是 {project.platform}，运行时是 {project.runtime}。",
        }

    if intent == "review_or_fix":
        report = run_review_project(ProjectManager(workspace_root).load_project(project_name).path)
        review_payload = report.get("report", {})
        return {
            "success": True,
            "mode": "review",
            "execution_plan": execution_plan.model_dump(mode="json"),
            "report": report,
            "message": _build_review_message(review_payload),
        }

    if intent == "debug_project":
        project = ProjectManager(workspace_root).load_project(project_name)
        if plan_only or dry_run:
            return {
                "success": True,
                "mode": "plan",
                "execution_plan": execution_plan.model_dump(mode="json"),
                "engineering_context": engineering.model_dump(mode="json") if engineering else {},
            }
        result = run_debug_workflow(
            config=config,
            project_root=project_root,
            project_path=project.path,
            probe=None if no_flash else None,
            port="" if no_monitor else "",
            clean=False,
        )
        return {
            "success": bool(result.success),
            "mode": "execute",
            "execution_plan": execution_plan.model_dump(mode="json"),
            "workflow": result.model_dump(mode="json"),
        }

    if intent == "generate_driver":
        chip, interface = _infer_driver_request(task=task, engineering=engineering)
        if not chip or not interface:
            return {
                "success": False,
                "mode": "plan",
                "execution_plan": execution_plan.model_dump(mode="json"),
                "engineering_context": engineering.model_dump(mode="json") if engineering else {},
                "message": "Need at least a chip/device name and interface before generating a driver.",
            }
        if plan_only or dry_run:
            return {
                "success": True,
                "mode": "plan",
                "execution_plan": execution_plan.model_dump(mode="json"),
                "engineering_context": engineering.model_dump(mode="json") if engineering else {},
                "driver_request": {"chip": chip, "interface": interface},
            }
        pipeline = run_generate_driver_loop(
            config=config,
            project_root=project_root,
            chip=chip,
            interface=interface,
            doc_summary=engineering.document_summary if engineering else task,
        )
        return {
            "success": bool(pipeline.success),
            "mode": "execute",
            "execution_plan": execution_plan.model_dump(mode="json"),
            "engineering_context": engineering.model_dump(mode="json") if engineering else {},
            "driver_request": {"chip": chip, "interface": interface},
            "workflow": pipeline.model_dump(mode="json"),
        }

    if intent == "forge_project":
        project = ProjectManager(workspace_root).load_project(project_name)
        workflow = run_forge_project(
            config=config,
            project_root=project_root,
            project=project,
            requirement=task,
            driver_library_root=driver_library_root,
            plan_only=plan_only or dry_run,
            build=not (no_build or dry_run or plan_only),
            no_flash=no_flash,
            no_monitor=no_monitor,
            docs=docs,
            doc_query=task,
        )
        return {
            "success": workflow.success,
            "mode": "execute" if not (plan_only or dry_run) else "plan",
            "execution_plan": execution_plan.model_dump(mode="json"),
            "engineering_context": engineering.model_dump(mode="json") if engineering else {},
            "workflow": workflow.model_dump(mode="json"),
        }

    return {
        "success": True,
        "mode": "plan",
        "execution_plan": execution_plan.model_dump(mode="json"),
        "engineering_context": engineering.model_dump(mode="json") if engineering else {},
    }


def _build_engineering_context(*, driver_library_root: str, docs: list[str], query: str):
    analyzer = DocumentEngineeringAnalyzer(Path(driver_library_root).resolve() / "knowledge_base")
    return analyzer.analyze(docs=docs, query=query)


def _infer_driver_request(*, task: str, engineering: EngineeringContext | None) -> tuple[str, str]:
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


def _build_explain_message(*, task: str, engineering: EngineeringContext | None) -> str:
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


def _build_review_message(report: dict) -> str:
    total = int(report.get("total_issues", 0) or 0)
    critical = int(report.get("critical_count", 0) or 0)
    errors = int(report.get("error_count", 0) or 0)
    warnings = int(report.get("warning_count", 0) or 0)
    if report.get("passed", False):
        return f"我已经审查了当前项目代码，结果通过，没有阻塞性问题。当前共有 {warnings} 个警告。"
    return f"我已经审查了当前项目代码，结果未通过。当前共有 {total} 个问题，其中严重 {critical} 个、错误 {errors} 个、警告 {warnings} 个。"
