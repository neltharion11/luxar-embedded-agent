from __future__ import annotations

from luxar.core.flash_system import FlashSystem
from luxar.core.config_manager import AgentConfig
from luxar.core.toolchain_manager import ToolchainManager
from luxar.platforms.stm32_adapter import STM32CubeMXAdapter


def run_flash_project(
    project_path: str,
    config: AgentConfig,
    project_root: str,
    probe: str | None = None,
):
    toolchain_manager = ToolchainManager(config=config, project_root=project_root)
    system = FlashSystem(
        STM32CubeMXAdapter(
            toolchain_manager=toolchain_manager,
            openocd_interface=config.flash.openocd_interface,
            openocd_target=config.flash.openocd_target,
        )
    )
    return system.flash_project(project_path, probe=probe)


