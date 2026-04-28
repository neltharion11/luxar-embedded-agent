from __future__ import annotations

from pathlib import Path

from luxar.core.review_engine import ReviewEngine


def run_review_project(project_path: str, file_path: str | None = None):
    engine = ReviewEngine(project_path)
    if file_path:
        target = Path(file_path)
        if not target.is_absolute():
            target = Path(project_path) / target
        report = engine.review_file(str(target.resolve()))
        reviewed_files = [str(target.resolve())]
    else:
        reviewed_files = engine.discover_project_files()
        report = engine.review_files(reviewed_files)

    return {
        "project_path": str(Path(project_path).resolve()),
        "reviewed_files": reviewed_files,
        "report": report.model_dump(mode="json"),
    }


