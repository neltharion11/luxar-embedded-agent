"""监控 Fake：按预设顺序返回监控证据，记录调用但不接触任何硬件。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from luxar.domain.devices import MonitorEvidence


class FakeMonitor:
    def __init__(
        self,
        evidence_sequence: Sequence[MonitorEvidence],
    ) -> None:
        # 复制为内部队列，避免调用方后来修改原 Sequence 干扰测试。
        self._remaining_evidence = list(evidence_sequence)
        self.calls: list[tuple[Path, str, int]] = []

    def monitor(
        self,
        project_path: Path,
        port: str,
        timeout_seconds: int,
    ) -> MonitorEvidence:
        self.calls.append((project_path, port, timeout_seconds))

        if not self._remaining_evidence:
            raise RuntimeError(
                "FakeMonitor has no configured evidence remaining"
            )

        return self._remaining_evidence.pop(0)
