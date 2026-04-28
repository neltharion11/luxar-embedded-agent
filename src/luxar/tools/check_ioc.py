from __future__ import annotations

from luxar.platforms.stm32_adapter import STM32CubeMXAdapter


def run_check_ioc(project_path: str) -> dict:
    adapter = STM32CubeMXAdapter()
    return adapter.check_project_config(project_path)



