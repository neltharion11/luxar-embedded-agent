from pathlib import Path

import pytest

from luxar.application.hardware_smoke import (
    qualification_observation_from_hardware_smoke,
    run_hardware_smoke,
)
from luxar.domain.devices import (
    FlashEvidence,
    MonitorEvidence,
    SerialPortInfo,
)
from luxar.domain.evidence import BuildEvidence


class FakeHardware:
    def __init__(self) -> None:
        self.build_calls = 0
        self.flash_calls = 0
        self.monitor_calls = 0

    def discover_serial_ports(self) -> list[SerialPortInfo]:
        return [
            SerialPortInfo(
                name="COM4",
                description="USB-SERIAL CH340",
                hardware_id="USB VID:PID=1A86:7523",
            )
        ]

    def build(self, project_path: Path) -> BuildEvidence:
        self.build_calls += 1
        return BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
        )

    def flash(self, project_path: Path, port: str) -> FlashEvidence:
        self.flash_calls += 1
        return FlashEvidence(
            success=True,
            command=["idf.py", "-p", port, "flash"],
            return_code=0,
            port=port,
        )

    def monitor(
        self,
        project_path: Path,
        port: str,
        timeout_seconds: int,
    ) -> MonitorEvidence:
        self.monitor_calls += 1
        return MonitorEvidence(
            command=["idf.py", "-p", port, "monitor"],
            port=port,
            capture_timeout_seconds=timeout_seconds,
            captured_log="diagnostics: status=ok errors=0 watchdog=armed",
            terminated_by_timeout=True,
        )


def test_hardware_smoke_refuses_every_tool_without_explicit_approval(
    tmp_path: Path,
) -> None:
    hardware = FakeHardware()

    with pytest.raises(PermissionError):
        run_hardware_smoke(
            project_path=tmp_path,
            target_chip="esp32",
            port="COM4",
            approved=False,
            build_executor=hardware,
            flasher=hardware,
            monitor=hardware,
            expected_log_markers=["status=ok"],
        )

    assert hardware.build_calls == 0
    assert hardware.flash_calls == 0
    assert hardware.monitor_calls == 0


def test_hardware_smoke_requires_build_flash_and_device_log_evidence(
    tmp_path: Path,
) -> None:
    hardware = FakeHardware()

    report = run_hardware_smoke(
        project_path=tmp_path,
        target_chip="esp32",
        port="COM4",
        approved=True,
        build_executor=hardware,
        flasher=hardware,
        monitor=hardware,
        expected_log_markers=["status=ok", "watchdog=armed"],
    )

    assert report.passed is True
    assert report.evidence_id.startswith("hardware-smoke:")
    assert hardware.build_calls == 1
    assert hardware.flash_calls == 1
    assert hardware.monitor_calls == 1
    observation = qualification_observation_from_hardware_smoke(report)
    assert observation.passed is True
    assert observation.evidence_ids == [report.evidence_id]
