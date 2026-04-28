from __future__ import annotations

from luxar.core.config_manager import AgentConfig
from luxar.core.driver_pipeline import DriverPipeline


def run_generate_driver_loop(
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

