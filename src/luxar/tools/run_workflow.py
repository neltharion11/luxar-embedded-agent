from __future__ import annotations

from luxar.core.config_manager import AgentConfig
from luxar.core.workflow_engine import WorkflowEngine


def run_driver_workflow(
    config: AgentConfig,
    project_root: str,
    chip: str,
    interface: str,
    doc_summary: str,
    register_summary: str = "",
    vendor: str = "",
    device: str = "",
    output_dir: str = "",
    max_fix_iterations: int | None = None,
):
    engine = WorkflowEngine(config=config, project_root=project_root)
    return engine.run_driver_workflow(
        chip=chip,
        interface=interface,
        doc_summary=doc_summary,
        register_summary=register_summary,
        vendor=vendor,
        device=device,
        output_dir=output_dir,
        max_fix_iterations=max_fix_iterations,
    )


def run_debug_workflow(
    config: AgentConfig,
    project_root: str,
    project_path: str,
    probe: str | None = None,
    port: str = "",
    clean: bool = False,
    lines: int = 10,
    baudrate: int | None = None,
):
    engine = WorkflowEngine(config=config, project_root=project_root)
    return engine.run_debug_workflow(
        project_path=project_path,
        probe=probe,
        port=port,
        clean=clean,
        lines=lines,
        baudrate=baudrate,
    )

