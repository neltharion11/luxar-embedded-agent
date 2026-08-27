"""Deterministic, evidence-backed real-hardware smoke workflow."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.devices import FlashEvidence, MonitorEvidence
from luxar.domain.evidence import BuildEvidence
from luxar.ports.espidf import EspIdfPort
from luxar.ports.espidf_device import EspIdfFlashPort, EspIdfMonitorPort

if TYPE_CHECKING:
    from luxar.application.runtime_qualification import QualificationObservation


_FATAL_DIAGNOSTICS = frozenset(
    {"panic", "abort", "assert", "watchdog", "boot_loop", "error"}
)


class HardwareSmokeReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    target_chip: str
    port: str
    hardware_id: str
    approval_granted: bool
    build_evidence: BuildEvidence
    flash_evidence: FlashEvidence
    monitor_evidence: MonitorEvidence
    expected_log_markers: list[str] = Field(default_factory=list)
    missing_log_markers: list[str] = Field(default_factory=list)
    fatal_diagnostic_kinds: list[str] = Field(default_factory=list)
    passed: bool
    evidence_id: str


def evaluate_hardware_smoke(
    *,
    target_chip: str,
    port: str,
    hardware_id: str,
    approval_granted: bool,
    build_evidence: BuildEvidence,
    flash_evidence: FlashEvidence,
    monitor_evidence: MonitorEvidence,
    expected_log_markers: list[str],
) -> HardwareSmokeReport:
    """Evaluate independently collected tool evidence without rerunning tools."""

    missing = [
        marker
        for marker in expected_log_markers
        if marker not in monitor_evidence.captured_log
    ]
    fatal = sorted(
        {
            diagnostic.kind
            for diagnostic in monitor_evidence.diagnostics
            if diagnostic.kind in _FATAL_DIAGNOSTICS
        }
    )
    passed = bool(
        approval_granted
        and build_evidence.success
        and flash_evidence.success
        and not missing
        and not fatal
    )
    digest = hashlib.sha256(
        "\n".join(
            [
                target_chip,
                port,
                hardware_id,
                str(build_evidence.return_code),
                str(flash_evidence.return_code),
                *expected_log_markers,
                *missing,
                *fatal,
            ]
        ).encode("utf-8")
    ).hexdigest()
    return HardwareSmokeReport(
        target_chip=target_chip,
        port=port,
        hardware_id=hardware_id,
        approval_granted=approval_granted,
        build_evidence=build_evidence,
        flash_evidence=flash_evidence,
        monitor_evidence=monitor_evidence,
        expected_log_markers=expected_log_markers,
        missing_log_markers=missing,
        fatal_diagnostic_kinds=fatal,
        passed=passed,
        evidence_id=f"hardware-smoke:{digest}",
    )


def run_hardware_smoke(
    *,
    project_path: Path,
    target_chip: str,
    port: str,
    approved: bool,
    build_executor: EspIdfPort,
    flasher: EspIdfFlashPort,
    monitor: EspIdfMonitorPort,
    expected_log_markers: list[str],
    monitor_timeout_seconds: int = 12,
) -> HardwareSmokeReport:
    """Build, flash and monitor only after an explicit approval decision."""

    if not approved:
        raise PermissionError("真实硬件 smoke 未获得烧录审批")
    discovered = {
        item.name: item for item in flasher.discover_serial_ports()
    }
    if port not in discovered:
        raise ValueError(f"审批串口当前不可用: {port}")

    build = build_executor.build(project_path)
    if not build.success:
        raise RuntimeError("真实硬件 smoke 构建失败，未执行烧录")
    combined = getattr(flasher, "flash_and_monitor", None)
    if callable(combined):
        flash, monitor_evidence = combined(
            project_path,
            port,
            monitor_timeout_seconds,
        )
    else:
        flash = flasher.flash(project_path, port)
        monitor_evidence = None
    if not flash.success:
        raise RuntimeError("真实硬件 smoke 烧录失败，未执行监控")
    if monitor_evidence is None:
        monitor_evidence = monitor.monitor(
            project_path,
            port,
            monitor_timeout_seconds,
        )
    return evaluate_hardware_smoke(
        target_chip=target_chip,
        port=port,
        hardware_id=discovered[port].hardware_id,
        approval_granted=True,
        build_evidence=build,
        flash_evidence=flash,
        monitor_evidence=monitor_evidence,
        expected_log_markers=expected_log_markers,
    )


def qualification_observation_from_hardware_smoke(
    report: HardwareSmokeReport,
) -> "QualificationObservation":
    from luxar.application.runtime_qualification import QualificationObservation

    return QualificationObservation(
        gate_id="real_hardware_smoke",
        passed=report.passed,
        evidence_ids=[report.evidence_id] if report.passed else [],
        note=(
            f"{report.target_chip} on {report.port} passed"
            if report.passed
            else (
                f"missing={report.missing_log_markers}; "
                f"fatal={report.fatal_diagnostic_kinds}"
            )
        ),
    )


__all__ = [
    "HardwareSmokeReport",
    "evaluate_hardware_smoke",
    "qualification_observation_from_hardware_smoke",
    "run_hardware_smoke",
]
