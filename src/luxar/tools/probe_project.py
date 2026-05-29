from __future__ import annotations

from luxar.core.config_manager import AgentConfig
from luxar.core.probe_system import ProbeSystem
from luxar.core.toolchain_manager import ToolchainManager
from luxar.platforms.stm32_adapter import STM32CubeMXAdapter


def run_probe_project(
    project_path: str,
    config: AgentConfig,
    project_root: str,
    probe_type: str = "i2c",
):
    toolchain_manager = ToolchainManager(config=config, project_root=project_root)
    system = ProbeSystem(
        STM32CubeMXAdapter(
            toolchain_manager=toolchain_manager,
            openocd_interface=config.flash.openocd_interface,
            openocd_target=config.flash.openocd_target,
        )
    )
    return system.probe_project(project_path, probe_type=probe_type)
