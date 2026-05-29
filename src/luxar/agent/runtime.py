from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from luxar.agent.context_builder import RuntimeWorkspace
from luxar.agent.diagnostics import (
    Evidence,
    EscalationDecision,
    check_escalation,
    collect_evidence,
    summarize_runtime_state,
)
from luxar.agent.explain import explain_runtime_model
from luxar.agent.planner import build_runtime_plan
from luxar.agent.policy import evaluate_promotion
from luxar.core.config_manager import ConfigManager
from luxar.memory.lesson_store import LessonStore
from luxar.skills.manager import SkillManagerVNext


@dataclass(slots=True)
class RuntimeRunResult:
    success: bool
    task: str
    project: str
    workspace: dict[str, str]
    plan: dict[str, Any]
    selected_skills: list[dict[str, Any]]
    selected_executable_skills: list[dict[str, Any]]
    lesson_matches: list[dict[str, Any]]
    diagnostics: dict[str, int]
    evidence: dict[str, Any] = field(default_factory=dict)
    escalation: dict[str, Any] | None = None
    lessons_recorded: list[str] = field(default_factory=list)
    promotion_result: dict[str, Any] | None = None
    execution_results: list[dict[str, Any]] = field(default_factory=list)
    executed_skills: int = field(default=0)
    mode: str = "runtime"

    def model_dump(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "task": self.task,
            "project": self.project,
            "workspace": self.workspace,
            "plan": self.plan,
            "selected_skills": self.selected_skills,
            "selected_executable_skills": self.selected_executable_skills,
            "lesson_matches": self.lesson_matches,
            "diagnostics": self.diagnostics,
            "evidence": self.evidence,
            "escalation": self.escalation,
            "lessons_recorded": self.lessons_recorded,
            "promotion_result": self.promotion_result,
            "execution_results": self.execution_results,
            "executed_skills": self.executed_skills,
            "mode": self.mode,
        }


def run_runtime_task(
    task: str,
    project: str = "",
    manager: ConfigManager | None = None,
    *,
    evidence: Evidence | None = None,
    is_hardware_task: bool = False,
    consecutive_failures: int = 0,
) -> dict[str, Any]:
    cfg_manager = manager or ConfigManager()
    workspace = RuntimeWorkspace.from_manager(cfg_manager)
    workspace.ensure_layout()
    skill_manager = SkillManagerVNext(workspace.skills_root)
    lesson_store = LessonStore(workspace.lesson_root)

    skills = skill_manager.match(task, limit=5)
    executable_skills = [item for item in skills if item.get("metadata", {}).get("mode") == "executable"]
    lessons = lesson_store.search(task)

    evidence_obj = evidence or collect_evidence()
    missing_coverage = len(skills) == 0

    escalation: EscalationDecision | None = None
    if missing_coverage or consecutive_failures > 0 or is_hardware_task:
        escalation = check_escalation(
            evidence=evidence_obj,
            consecutive_failures=consecutive_failures,
            is_hardware_task=is_hardware_task,
            missing_skill_coverage=missing_coverage,
        )

    lessons_recorded: list[str] = []
    if consecutive_failures > 0:
        lesson_id = lesson_store.record(
            task=task,
            outcome="failure",
            summary=f"Task failed after {consecutive_failures} attempt(s). Evidence: {evidence_obj.summary()}",
            tags=["runtime", "failure"],
        )
        lessons_recorded.append(lesson_id)

    promotion_result: dict[str, Any] | None = None
    if consecutive_failures >= 2 and lessons_recorded:
        decision = evaluate_promotion(
            kind="lesson",
            current_state="draft",
            evidence_count=consecutive_failures,
            attributes={"promotable": True},
        )
        promotion_result = {
            "kind": "lesson",
            "allowed": decision.allowed,
            "reason": decision.reason,
            "target_state": decision.target_state,
        }

    result = RuntimeRunResult(
        success=not (escalation and escalation.should_escalate),
        task=task,
        project=project,
        workspace=workspace.snapshot(),
        plan=build_runtime_plan(task),
        selected_skills=skills,
        selected_executable_skills=executable_skills,
        lesson_matches=lessons,
        diagnostics=summarize_runtime_state(
            skills=len(skill_manager.list_skills()),
            executable_skills=len(skill_manager.executable_skills()),
            lessons=len(lesson_store.list_lessons()),
        ),
        evidence=evidence_obj.summary(),
        escalation=(
            {
                "should_escalate": escalation.should_escalate,
                "reason": escalation.reason,
                "options": escalation.options,
                "recommendation": escalation.recommendation,
            }
            if escalation
            else None
        ),
        lessons_recorded=lessons_recorded,
        promotion_result=promotion_result,
    )
    # Execute matched executable skills
    execution_results = []
    if executable_skills and project:
        from luxar.tools.skills_tool import skill_execute as _exec
        for sk in executable_skills:
            try:
                r = _exec(
                    name=sk["name"],
                    category=sk.get("category", ""),
                    project=project,
                )
                execution_results.append({
                    "skill": sk["name"],
                    "success": r.get("success", False),
                    "result": r,
                })
            except Exception as ex:
                execution_results.append({
                    "skill": sk["name"],
                    "success": False,
                    "error": str(ex),
                })
    result.execution_results = execution_results
    result.executed_skills = len(execution_results)


    return result.model_dump()


def explain_runtime() -> dict[str, Any]:
    return explain_runtime_model()
