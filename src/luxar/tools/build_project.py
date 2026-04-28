from __future__ import annotations

from pathlib import Path

from luxar.core.build_system import BuildSystem
from luxar.core.config_manager import AgentConfig
from luxar.core.review_engine import ReviewEngine
from luxar.core.toolchain_manager import ToolchainManager
from luxar.models.schemas import BuildResult
from luxar.platforms.stm32_adapter import STM32CubeMXAdapter


def run_build_project(
    project_path: str,
    config: AgentConfig,
    project_root: str,
    clean: bool = False,
    skip_review: bool = False,
):
    if config.review.enabled and not skip_review:
        review_engine = ReviewEngine(project_path)
        review_report = review_engine.review_files(review_engine.discover_project_files())
        if not review_report.passed:
            issue_summaries = [
                f"{issue.rule_id}@{Path(issue.file).name}:{issue.line} {issue.message}"
                for issue in review_report.issues
                if issue.severity in {"critical", "error"}
            ]
            return BuildResult(
                success=False,
                command=[],
                return_code=-2,
                stderr="Pre-build review failed. Re-run with --skip-review to bypass the quality gate.",
                errors=issue_summaries or ["review_failed"],
                warnings=[
                    f"{issue.rule_id}@{Path(issue.file).name}:{issue.line} {issue.message}"
                    for issue in review_report.issues
                    if issue.severity == "warning"
                ],
            )

    toolchain_manager = ToolchainManager(config=config, project_root=project_root)
    system = BuildSystem(
        STM32CubeMXAdapter(
            toolchain_manager=toolchain_manager,
            openocd_interface=config.flash.openocd_interface,
            openocd_target=config.flash.openocd_target,
        )
    )
    return system.build_project(project_path, clean=clean)


