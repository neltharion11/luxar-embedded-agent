from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from luxar.agent.context_builder import RuntimeWorkspace
from luxar.agent.diagnostics import summarize_runtime_state
from luxar.agent.explain import explain_runtime_model
from luxar.agent.planner import build_runtime_plan
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
            "mode": self.mode,
        }


def run_runtime_task(task: str, project: str = "", manager: ConfigManager | None = None) -> dict[str, Any]:
    cfg_manager = manager or ConfigManager()
    workspace = RuntimeWorkspace.from_manager(cfg_manager)
    workspace.ensure_layout()
    skill_manager = SkillManagerVNext(workspace.skills_root)
    lesson_store = LessonStore(workspace.lesson_root)

    skills = skill_manager.match(task)
    executable_skills = [item for item in skills if item.get("metadata", {}).get("mode") == "executable"]
    lessons = lesson_store.search(task)
    result = RuntimeRunResult(
        success=True,
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
    )
    return result.model_dump()


def explain_runtime() -> dict[str, Any]:
    return explain_runtime_model()
