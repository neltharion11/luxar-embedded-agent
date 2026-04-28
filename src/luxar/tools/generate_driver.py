from __future__ import annotations

from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.driver_generator import DriverGenerator


def run_generate_driver(
    config: AgentConfig,
    project_root: str,
    chip: str,
    interface: str,
    doc_summary: str,
    register_summary: str = "",
    vendor: str = "",
    device: str = "",
    output_dir: str = "",
):
    root = Path(project_root).resolve()
    if output_dir:
        resolved_output = Path(output_dir)
        if not resolved_output.is_absolute():
            resolved_output = root / resolved_output
    else:
        resolved_output = (
            root
            / config.agent.driver_library
            / "generated"
            / interface.lower()
            / (vendor.strip().lower() or "generic")
            / ((device.strip() or chip.strip()).lower())
        )

    generator = DriverGenerator(config, project_root=root)
    return generator.generate_driver(
        chip=chip,
        interface=interface,
        protocol_summary=doc_summary,
        register_summary=register_summary,
        output_dir=str(resolved_output),
        vendor=vendor,
        device=device,
    )

