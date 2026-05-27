from __future__ import annotations

from luxar.core.config_manager import AgentConfig
from pathlib import Path

from luxar.core.review_engine import ReviewEngine
from luxar.agent.workers._driver_pipeline import DriverPipeline
from luxar.tools.build_project import run_build_project
from luxar.agent.workers._forge_project import run_forge_project, run_forge_project_stream
from luxar.tools.run_workflow import run_debug_workflow


def run_review(project_path: str, file_path: str | None = None) -> dict:
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
        'project_path': str(Path(project_path).resolve()),
        'reviewed_files': reviewed_files,
        'report': report.model_dump(mode='json'),
    }


def run_generate_driver(
    *,
    config: AgentConfig,
    project_root: str,
    chip: str,
    interface: str,
    doc_summary: str,
    register_summary: str = '',
    vendor: str = '',
    device: str = '',
    output_dir: str = '',
    max_fix_iterations: int | None = None,
):
    pipeline = DriverPipeline(config=config, project_root=project_root)
    return pipeline.generate_review_fix(
        chip=chip,
        interface=interface,
        protocol_summary=doc_summary,
        register_summary=register_summary,
        vendor=vendor,
        device=device,
        output_dir=output_dir,
        max_fix_iterations=max_fix_iterations,
    )


def run_build(
    *,
    project_path: str,
    config: AgentConfig,
    project_root: str,
    clean: bool,
    skip_review: bool,
):
    return run_build_project(
        project_path=project_path,
        config=config,
        project_root=project_root,
        clean=clean,
        skip_review=skip_review,
    )


def run_debug(
    *,
    config: AgentConfig,
    project_root: str,
    project_path: str,
    probe,
    port: str,
    clean: bool,
):
    return run_debug_workflow(
        config=config,
        project_root=project_root,
        project_path=project_path,
        probe=probe,
        port=port,
        clean=clean,
    )


def run_forge(
    *,
    config: AgentConfig,
    project_root: str,
    project,
    requirement: str,
    driver_library_root: str,
    plan_only: bool,
    build: bool,
    no_flash: bool,
    no_monitor: bool,
    docs: list[str],
    doc_query: str,
):
    return run_forge_project(
        config=config,
        project_root=project_root,
        project=project,
        requirement=requirement,
        driver_library_root=driver_library_root,
        plan_only=plan_only,
        build=build,
        no_flash=no_flash,
        no_monitor=no_monitor,
        docs=docs,
        doc_query=doc_query,
    )


def run_forge_stream(
    *,
    config: AgentConfig,
    project_root: str,
    project,
    requirement: str,
    driver_library_root: str,
    plan_only: bool,
    build: bool,
    no_flash: bool,
    no_monitor: bool,
    docs: list[str],
    doc_query: str,
):
    return run_forge_project_stream(
        config=config,
        project_root=project_root,
        project=project,
        requirement=requirement,
        driver_library_root=driver_library_root,
        plan_only=plan_only,
        build=build,
        no_flash=no_flash,
        no_monitor=no_monitor,
        docs=docs,
        doc_query=doc_query,
    )
