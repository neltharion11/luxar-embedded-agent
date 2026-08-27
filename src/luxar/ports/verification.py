"""受控组件测试与固件资源检查工具接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from luxar.domain.agent.verification import (
    ComponentTestEvidence,
    ComponentTestSpec,
    FirmwareResourceEvidence,
)
from luxar.domain.agent.runtime_verification import (
    ProtocolProbeEvidence,
    ProtocolProbeSpec,
    RuntimeScenarioEvidence,
    RuntimeScenarioSpec,
)


VerificationToolErrorCategory = Literal[
    "environment",
    "configuration",
    "timeout",
    "execution",
    "evidence",
]


class VerificationToolError(RuntimeError):
    def __init__(
        self,
        category: VerificationToolErrorCategory,
        message: str,
    ) -> None:
        super().__init__(message)
        self.category = category


class ComponentTestPort(Protocol):
    def run_component_test(
        self,
        project_path: Path,
        spec: ComponentTestSpec,
    ) -> ComponentTestEvidence:
        ...


class FirmwareInspectorPort(Protocol):
    def inspect_firmware(self, project_path: Path) -> FirmwareResourceEvidence:
        ...


class ProtocolProbePort(Protocol):
    def run_protocol_probe(
        self,
        project_path: Path,
        spec: ProtocolProbeSpec,
    ) -> ProtocolProbeEvidence:
        ...


class RuntimeScenarioPort(Protocol):
    def run_runtime_scenario(
        self,
        project_path: Path,
        spec: RuntimeScenarioSpec,
    ) -> RuntimeScenarioEvidence:
        ...


__all__ = [
    "ComponentTestPort",
    "FirmwareInspectorPort",
    "ProtocolProbePort",
    "RuntimeScenarioPort",
    "VerificationToolError",
    "VerificationToolErrorCategory",
]
