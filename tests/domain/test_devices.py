import pytest
from pydantic import ValidationError

from luxar.domain.devices import (
    ApprovalRequest,
    DeviceDiagnosis,
    DeviceLogDiagnostic,
    FlashEvidence,
    MonitorEvidence,
    SerialPortInfo,
)


def test_serial_port_info_accepts_safe_name() -> None:
    port = SerialPortInfo(
        name="COM3",
        description="USB Serial",
        hardware_id="USB VID:PID=1A86:7523",
    )

    assert port.name == "COM3"


@pytest.mark.parametrize("name", ["", "COM 3", "COM3\n", "\tCOM3"])
def test_serial_port_info_rejects_empty_or_whitespace_name(
    name: str,
) -> None:
    with pytest.raises(ValidationError):
        SerialPortInfo(name=name)


def test_flash_evidence_accepts_success() -> None:
    evidence = FlashEvidence(
        success=True,
        command=["idf.py", "-p", "COM3", "flash"],
        return_code=0,
        port="COM3",
    )

    assert evidence.error_category is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"return_code": 1},
        {"return_code": 1, "error_category": "serial"},
        {"return_code": 0, "error_category": "serial"},
        {"port": "COM 3"},
    ],
)
def test_flash_evidence_rejects_inconsistent_or_unsafe_values(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        FlashEvidence(
            success=kwargs.get("success", True),
            command=["idf.py", "-p", "COM3", "flash"],
            return_code=kwargs.get("return_code", 0),
            port=kwargs.get("port", "COM3"),
            error_category=kwargs.get("error_category"),
        )


def test_flash_evidence_rejects_failure_with_zero_return_code() -> None:
    with pytest.raises(ValidationError):
        FlashEvidence(
            success=False,
            command=["idf.py", "-p", "COM3", "flash"],
            return_code=0,
            port="COM3",
        )


def test_approval_request_carries_only_controlled_fields() -> None:
    request = ApprovalRequest(
        project_name="blink",
        port="COM3",
        target_chip="esp32",
        summary="即将向串口设备烧录固件",
        step_description="flash_project",
        attempts=0,
    )

    assert set(request.model_dump()) == {
        "project_name",
        "port",
        "target_chip",
        "summary",
        "step_description",
        "attempts",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_name", ""),
        ("port", ""),
        ("summary", ""),
        ("step_description", ""),
        ("attempts", -1),
    ],
)
def test_approval_request_rejects_invalid_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        ApprovalRequest(
            **{
                "project_name": "blink",
                "port": "COM3",
                "summary": "即将烧录",
                "step_description": "flash_project",
                "attempts": 0,
                field: value,
            }
        )


def test_monitor_evidence_accepts_capture_window() -> None:
    evidence = MonitorEvidence(
        command=["idf.py", "-p", "COM3", "monitor"],
        port="COM3",
        capture_timeout_seconds=10,
        captured_log="boot ok",
        terminated_by_timeout=True,
        diagnostics=[
            DeviceLogDiagnostic(
                kind="boot_loop",
                summary="重复复位",
                lines=["rst:0x1", "rst:0x1"],
            )
        ],
    )

    assert evidence.terminated_by_timeout is True
    assert len(evidence.diagnostics) == 1


def test_monitor_evidence_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        MonitorEvidence(
            command=["idf.py", "-p", "COM3", "monitor"],
            port="COM 3",
            capture_timeout_seconds=10,
            terminated_by_timeout=False,
        )

    with pytest.raises(ValidationError):
        MonitorEvidence(
            command=["idf.py", "-p", "COM3", "monitor"],
            port="COM3",
            capture_timeout_seconds=0,
            terminated_by_timeout=False,
        )


def test_device_diagnosis_accepts_healthy() -> None:
    diagnosis = DeviceDiagnosis(
        healthy=True,
        repair_needed=False,
        summary="运行正常",
    )

    assert diagnosis.healthy is True


def test_device_diagnosis_accepts_repair_needed() -> None:
    diagnosis = DeviceDiagnosis(
        healthy=False,
        repair_needed=True,
        summary="看门狗超时",
        findings=["task_wdt 超时"],
    )

    assert diagnosis.repair_needed is True


def test_device_diagnosis_rejects_healthy_with_repair() -> None:
    with pytest.raises(ValidationError, match="cannot require a repair"):
        DeviceDiagnosis(
            healthy=True,
            repair_needed=True,
            summary="矛盾诊断",
        )


def test_device_diagnosis_rejects_empty_summary() -> None:
    with pytest.raises(ValidationError):
        DeviceDiagnosis(
            healthy=True,
            repair_needed=False,
            summary="",
        )
