from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from luxar.core.config_manager import ConfigManager


@dataclass(slots=True)
class RuntimeWorkspace:
    project_root: Path
    projects_root: Path
    driver_library_root: Path
    legacy_skill_root: Path
    skills_root: Path
    lesson_root: Path
    memory_root: Path
    prompts_root: Path

    @classmethod
    def from_manager(cls, manager: ConfigManager) -> "RuntimeWorkspace":
        return cls(
            project_root=manager.project_root(),
            projects_root=manager.workspace_root(),
            driver_library_root=manager.driver_library_root(),
            legacy_skill_root=manager.skill_library_root(),
            skills_root=manager.skills_root(),
            lesson_root=manager.lesson_library_root(),
            memory_root=manager.memory_root(),
            prompts_root=manager.prompts_root(),
        )

    def ensure_layout(self) -> None:
        for root in (
            self.skills_root,
            self.lesson_root,
            self.memory_root,
            self.prompts_root,
        ):
            root.mkdir(parents=True, exist_ok=True)

        for path in (
            self.skills_root / "protocols",
            self.skills_root / "boards",
            self.skills_root / "bringup",
            self.skills_root / "recovery",
            self.skills_root / "workflows",
            self.lesson_root / "draft",
            self.lesson_root / "promoted",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, str]:
        self.ensure_layout()
        return {
            "project_root": str(self.project_root),
            "projects_root": str(self.projects_root),
            "driver_library_root": str(self.driver_library_root),
            "legacy_skill_root": str(self.legacy_skill_root),
            "skills_root": str(self.skills_root),
            "lesson_root": str(self.lesson_root),
            "memory_root": str(self.memory_root),
            "prompts_root": str(self.prompts_root),
        }
