from __future__ import annotations

from luxar.core.uart_monitor import UartMonitor
from luxar.platforms.stm32_adapter import STM32CubeMXAdapter


def run_monitor_project(project_path: str, **kwargs):
    monitor = UartMonitor(STM32CubeMXAdapter())
    return monitor.monitor_project(project_path, **kwargs)


