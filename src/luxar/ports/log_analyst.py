"""日志分析 Port：规定“设备运行日志转结构化诊断”能力的最小接口。"""

from __future__ import annotations

from typing import Protocol

from luxar.domain.devices import DeviceDiagnosis, MonitorEvidence
from luxar.domain.requirements import FirmwareRequirement


class LogAnalystPort(Protocol):
    # 模型只能从脱敏日志中产出结构化诊断，不能宣称构建或烧录成功。
    def analyze(
        self,
        requirement: FirmwareRequirement,
        evidence: MonitorEvidence,
    ) -> DeviceDiagnosis:
        ...
