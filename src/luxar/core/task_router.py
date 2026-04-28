from __future__ import annotations

from luxar.models.schemas import ExecutionPlan, TaskIntent


class TaskRouter:
    def route(
        self,
        *,
        task: str,
        project: str = "",
        docs: list[str] | None = None,
        dry_run: bool = False,
        plan_only: bool = False,
    ) -> ExecutionPlan:
        docs = docs or []
        lowered = task.lower()
        intent = self._classify(task=lowered, has_project=bool(project), has_docs=bool(docs), dry_run=dry_run, plan_only=plan_only)
        steps = self._build_steps(intent_type=intent.intent_type, has_docs=bool(docs))
        missing: list[str] = []
        if intent.intent_type in {"forge_project", "debug_project", "review_or_fix", "project_status"} and not project:
            missing.append("Select a project before executing project-scoped actions.")
        return ExecutionPlan(
            intent=intent,
            project=project,
            docs=docs,
            steps=steps,
            missing_info_questions=missing,
            dry_run=dry_run,
            plan_only=plan_only,
        )

    def _classify(
        self,
        *,
        task: str,
        has_project: bool,
        has_docs: bool,
        dry_run: bool,
        plan_only: bool,
    ) -> TaskIntent:
        if any(token in task for token in ("explain", "what is", "how does", "how do", "why", "tell me", "接线", "引脚", "协议", "收发", "解释")):
            return TaskIntent(
                intent_type="explain",
                execution_mode="explain",
                required_capabilities=["document_analysis"],
                recommended_workflow="explain",
                confidence=0.85 if has_docs else 0.72,
                reason="Task is asking for explanation or engineering guidance rather than direct execution.",
            )
        if any(token in task for token in ("status", "toolchain", "git", "driver library", "skill", "workspace")):
            return TaskIntent(
                intent_type="project_status",
                execution_mode="explain",
                required_capabilities=["status"],
                recommended_workflow="status",
                confidence=0.9,
                reason="Task asks for project or environment status information.",
            )
        if any(token in task for token in ("build", "flash", "monitor", "debug", "编译", "烧录", "串口", "修")):
            return TaskIntent(
                intent_type="debug_project",
                execution_mode="execute" if has_project and not dry_run else "plan",
                required_capabilities=["build", "flash", "monitor", "debug"],
                recommended_workflow="workflow_debug",
                confidence=0.84,
                reason="Task is centered on build/flash/monitor/debug activity.",
            )
        if any(token in task for token in ("review", "fix", "lint", "warning", "error in file", "修复", "审查")):
            return TaskIntent(
                intent_type="review_or_fix",
                execution_mode="execute" if not dry_run else "plan",
                required_capabilities=["review", "fix"],
                recommended_workflow="review",
                confidence=0.82,
                reason="Task asks to review or repair source code.",
            )
        if any(token in task for token in ("driver", "protocol", "寄存器", "驱动")) and not any(token in task for token in ("project", "工程", "assemble", "forge")):
            return TaskIntent(
                intent_type="generate_driver",
                execution_mode="execute" if not dry_run and not plan_only else "plan",
                required_capabilities=["driver_generation"],
                recommended_workflow="workflow_driver",
                confidence=0.78,
                reason="Task is primarily about a device driver or protocol implementation.",
            )
        if has_docs or any(token in task for token in (
            "generate project", "create project", "new project", "工程", "forge",
            "blink", "blinking", "led", "gpio", "button", "uart", "sensor",
            "create app", "make a", "build a", "setup", "初始化",
            "生成项目", "做工程",
        )):
            return TaskIntent(
                intent_type="forge_project",
                execution_mode="plan" if plan_only or dry_run else "execute",
                required_capabilities=["document_analysis", "planning", "driver_resolution", "forge"],
                recommended_workflow="forge",
                confidence=0.88,
                reason="Task describes a project-level outcome that fits the forge workflow.",
            )
        # Default: if task mentions project/app, route to forge; otherwise explain
        if any(token in task for token in ("project", "app", "application", "工程", "程序")):
            return TaskIntent(
                intent_type="forge_project",
                execution_mode="plan" if plan_only or dry_run else "execute",
                required_capabilities=["planning", "forge"],
                recommended_workflow="forge",
                confidence=0.72,
                reason="Task describes a project-level outcome that fits the forge workflow.",
            )
        return TaskIntent(
            intent_type="explain",
            execution_mode="explain",
            required_capabilities=["document_analysis"],
            recommended_workflow="explain",
            confidence=0.6,
            reason="Defaulted to explanatory mode because the request is better answered before execution.",
        )

    def _build_steps(self, *, intent_type: str, has_docs: bool) -> list[str]:
        if intent_type == "forge_project":
            steps = []
            if has_docs:
                steps.extend(["parse_docs", "analyze_docs"])
            steps.extend(["plan", "resolve_drivers", "assemble", "generate_app", "review", "fix", "build", "flash", "monitor"])
            return steps
        if intent_type == "generate_driver":
            return ["analyze_docs", "retrieve", "decide", "reuse_or_generate", "review", "fix", "store"]
        if intent_type == "debug_project":
            return ["build", "diagnose", "recover_or_fix", "flash", "monitor"]
        if intent_type == "review_or_fix":
            return ["review", "fix"]
        if intent_type == "project_status":
            return ["status"]
        return ["analyze_docs", "explain"]
