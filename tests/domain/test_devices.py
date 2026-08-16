import pytest
from pydantic import ValidationError

from luxar.domain.devices import (
    ApprovalRequest,
    FlashEvidence,
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
