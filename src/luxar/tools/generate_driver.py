from __future__ import annotations

import re
from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.driver_generator import DriverGenerator


def _safe_path_component(value: str, fallback: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", " ", (value or "").strip().lower())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or fallback


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
            / _safe_path_component(interface, "generic")
            / _safe_path_component(vendor, "generic")
            / _safe_path_component(device or chip, "generated_driver")
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

