"""烧录 Fake：按预设顺序返回烧录证据，并记录串口发现调用，无硬件副作用。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from luxar.domain.devices import FlashEvidence, SerialPortInfo


class FakeFlasher:
    def __init__(
        self,
        evidence_sequence: Sequence[FlashEvidence],
        ports: Sequence[SerialPortInfo] = (),
    ) -> None:
        # 复制为内部队列，避免调用方后来修改原 Sequence 干扰测试。
        self._remaining_evidence = list(evidence_sequence)
        self._ports = list(ports)
        self.flash_calls: list[tuple[Path, str]] = []
        self.discovery_calls = 0

    def discover_serial_ports(self) -> list[SerialPortInfo]:
        self.discovery_calls += 1
        return list(self._ports)

    def flash(
        self,
        project_path: Path,
        port: str,
    ) -> FlashEvidence:
        self.flash_calls.append((project_path, port))

        if not self._remaining_evidence:
            raise RuntimeError(
                "FakeFlasher has no configured flash evidence remaining"
            )

        return self._remaining_evidence.pop(0)
