from __future__ import annotations

from pathlib import Path
from typing import Any


def project_status(project_path: Path, *, git_manager_cls) -> dict:
    status: dict[str, Any] = {}
    git_dir = project_path / ".git"
    if git_dir.exists():
        try:
            gm = git_manager_cls(str(project_path))
            status["git"] = {
                "branch": gm.repo.active_branch.name,
                "modified": len(gm.changed_files().get("modified", [])),
                "untracked": len(gm.changed_files().get("untracked", [])),
            }
        except Exception:
            status["git"] = {"error": "git failed"}
    build_dir = project_path / "build"
    status["has_build_dir"] = build_dir.exists()
    drivers_dir = project_path / "Drivers"
    status["has_drivers"] = drivers_dir.exists() and any(drivers_dir.iterdir())
    return status
