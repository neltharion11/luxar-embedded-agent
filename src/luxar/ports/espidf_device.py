"""设备 Port：规定串口发现、烧录与串口监控工具能力的最小接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from luxar.domain.devices import (
    FlashEvidence,
    MonitorEvidence,
    SerialPortInfo,
)


class EspIdfFlashPort(Protocol):
    # 只返回经过验证的串口描述；Adapter 负责平台模式校验和脱敏。
    def discover_serial_ports(self) -> list[SerialPortInfo]:
        ...

    # 烧录成功与否只能由真实 idf.py flash 的证据决定。
    def flash(
        self,
        project_path: Path,
        port: str,
    ) -> FlashEvidence:
        ...


class EspIdfFlashMonitorPort(Protocol):
    """单进程连续执行 flash → monitor，避免同一串口被抢占。"""

    def flash_and_monitor(
        self,
        project_path: Path,
        port: str,
        timeout_seconds: int,
    ) -> tuple[FlashEvidence, MonitorEvidence]:
        ...


class EspIdfMonitorPort(Protocol):
    # 在受控采集窗口内抓取设备串口日志，超时后必须终止整个进程树。
    def monitor(
        self,
        project_path: Path,
        port: str,
        timeout_seconds: int,
    ) -> MonitorEvidence:
        ...
