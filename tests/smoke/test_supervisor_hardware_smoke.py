"""Destructive real-board smoke test; requires explicit opt-in and port."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from luxar.adapters.espidf_cli import EspIdfCliAdapter
from luxar.adapters.espidf_device import EspIdfDeviceAdapter
from luxar.application.hardware_smoke import (
    HardwareSmokeReport,
    evaluate_hardware_smoke,
    run_hardware_smoke,
)
from luxar.toolchain import EspIdfToolchainManager


FIXTURE = Path(__file__).parents[1] / "fixtures" / "full_environment_node"


def test_supervisor_real_hardware_smoke_is_explicitly_opt_in(
    tmp_path: Path,
) -> None:
    if os.environ.get("LUXAR_RUN_HARDWARE_SMOKE") != "1":
        pytest.skip("set LUXAR_RUN_HARDWARE_SMOKE=1 for destructive board test")
    if os.environ.get("LUXAR_APPROVE_FLASH") != "1":
        pytest.fail("hardware smoke requested without LUXAR_APPROVE_FLASH=1")
    port = os.environ.get("LUXAR_HARDWARE_PORT", "").strip()
    if not port:
        pytest.fail("LUXAR_HARDWARE_PORT must identify the approved board")

    project = tmp_path / "full_environment_node"
    shutil.copytree(FIXTURE, project)
    manager = EspIdfToolchainManager(
        config_path=tmp_path / "toolchain.json",
    )
    if not manager.status.available or manager.command is None:
        pytest.skip("a complete ESP-IDF toolchain is not available")
    builder = EspIdfCliAdapter(
        idf_command=manager.command,
        allow_dependency_downloads=True,
        reconfigure_timeout_seconds=600,
        build_timeout_seconds=1200,
    )
    device = EspIdfDeviceAdapter(
        idf_command=manager.command,
        flash_timeout_seconds=300,
    )

    report = run_hardware_smoke(
        project_path=project,
        target_chip="esp32",
        port=port,
        approved=True,
        build_executor=builder,
        flasher=device,
        monitor=device,
        expected_log_markers=[
            "diagnostics: status=ok errors=0 watchdog=armed"
        ],
        monitor_timeout_seconds=15,
    )

    evidence_path = tmp_path / "hardware-smoke-report.json"
    evidence_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assert report.passed is True, evidence_path.read_text(encoding="utf-8")


def test_supervisor_hardware_smoke_monitor_recovery_is_opt_in(
    tmp_path: Path,
) -> None:
    if os.environ.get("LUXAR_RECOVER_HARDWARE_SMOKE") != "1":
        pytest.skip("set LUXAR_RECOVER_HARDWARE_SMOKE=1 for monitor recovery")
    evidence_path = Path(
        os.environ.get("LUXAR_HARDWARE_EVIDENCE_PATH", "")
    )
    if not evidence_path.is_file():
        pytest.fail("LUXAR_HARDWARE_EVIDENCE_PATH must reference prior evidence")
    previous = HardwareSmokeReport.model_validate_json(
        evidence_path.read_text(encoding="utf-8")
    )
    project = evidence_path.parent / "full_environment_node"
    manager = EspIdfToolchainManager(
        config_path=evidence_path.parent / "toolchain.json",
    )
    if not manager.status.available or manager.command is None:
        pytest.skip("a complete ESP-IDF toolchain is not available")
    device = EspIdfDeviceAdapter(idf_command=manager.command)
    monitor = device.monitor(project, previous.port, 15)
    report = evaluate_hardware_smoke(
        target_chip=previous.target_chip,
        port=previous.port,
        hardware_id=previous.hardware_id,
        approval_granted=previous.approval_granted,
        build_evidence=previous.build_evidence,
        flash_evidence=previous.flash_evidence,
        monitor_evidence=monitor,
        expected_log_markers=previous.expected_log_markers,
    )
    recovered_path = tmp_path / "recovered-hardware-smoke-report.json"
    recovered_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert report.passed is True, recovered_path.read_text(encoding="utf-8")
