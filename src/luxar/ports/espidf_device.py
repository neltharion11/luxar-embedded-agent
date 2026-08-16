"""设备 Port：规定串口发现与烧录工具能力的最小接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from luxar.domain.devices import FlashEvidence, SerialPortInfo


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
