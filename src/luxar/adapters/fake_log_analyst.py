"""日志分析 Fake：按预设顺序返回诊断，记录分析输入但不调用模型。"""

from __future__ import annotations

from typing import Sequence

from luxar.domain.devices import DeviceDiagnosis, MonitorEvidence
from luxar.domain.requirements import FirmwareRequirement


class FakeLogAnalyst:
    def __init__(
        self,
        diagnoses: Sequence[DeviceDiagnosis],
    ) -> None:
        self._remaining_diagnoses = list(diagnoses)
        self.calls: list[tuple[FirmwareRequirement, MonitorEvidence]] = []

    def analyze(
        self,
        requirement: FirmwareRequirement,
        evidence: MonitorEvidence,
    ) -> DeviceDiagnosis:
        self.calls.append((requirement, evidence))

        if not self._remaining_diagnoses:
            raise RuntimeError(
                "FakeLogAnalyst has no configured diagnosis remaining"
            )

        return self._remaining_diagnoses.pop(0)
