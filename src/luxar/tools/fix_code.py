from __future__ import annotations

from pathlib import Path

from luxar.core.code_fixer import CodeFixer
from luxar.core.config_manager import AgentConfig
from luxar.core.review_engine import ReviewEngine
from luxar.models.schemas import ReviewReport


def run_fix_code(
    config: AgentConfig,
    project_path: str,
    file_path: str,
    apply_changes: bool = True,
    review_report: ReviewReport | dict | None = None,
    build_errors: list[str] | None = None,
):
    project_root = Path(project_path).resolve()
    target = Path(file_path)
    if not target.is_absolute():
        target = project_root / target
    target = target.resolve()

    if review_report is None:
        review_engine = ReviewEngine(str(project_root))
        review_engine.config.review.layers.semantic_review = False
        review_report = review_engine.review_file(str(target))
    else:
        review_report = ReviewReport.model_validate(
            review_report.model_dump(mode="json") if hasattr(review_report, "model_dump") else review_report
        )
    fixer = CodeFixer(config)
    return fixer.fix_file(
        project_path=str(project_root),
        file_path=str(target),
        review_report=review_report,
        build_errors=build_errors,
        apply_changes=apply_changes,
    )

