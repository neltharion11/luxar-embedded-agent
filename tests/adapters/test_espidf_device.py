from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from luxar.adapters.espidf_device import EspIdfDeviceAdapter
from luxar.domain.devices import FlashEvidence, SerialPortInfo
from luxar.ports.espidf_errors import EspIdfError


class FakeCompletedProcess:
    def __init__(
        self,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeListPortInfo:
    def __init__(
        self,
        device: str,
        description: str = "",
        hwid: str = "",
    ) -> None:
        self.device = device
        self.description = description
        self.hwid = hwid


def _make_project(root: Path) -> Path:
    root.mkdir(exist_ok=True)
    (root / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n",
        encoding="utf-8",
    )
    return root


def _allow_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "luxar.adapters.espidf_common.shutil.which",
        lambda command: f"C:/tools/{command}",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("flash_timeout_seconds", 0),
        ("flash_timeout_seconds", -1),
        ("max_summary_chars", True),
    ],
)
def test_constructor_rejects_invalid_positive_integer_limits(
    field: str,
    value: int | bool,
) -> None:
    with pytest.raises(ValueError, match=f"{field} must be a positive integer"):
        EspIdfDeviceAdapter(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("command", [(), [""], ["python", "  "]])
def test_constructor_rejects_empty_command(
    command: list[str] | tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="idf_command"):
        EspIdfDeviceAdapter(idf_command=command)


@pytest.mark.parametrize(
    "port",
    ["COM3", "COM123"],
)
def test_discover_returns_only_safe_windows_ports(
    monkeypatch: pytest.MonkeyPatch,
    port: str,
) -> None:
    monkeypatch.setattr(
        "luxar.adapters.espidf_device.serial.tools.list_ports.comports",
        lambda: [
            FakeListPortInfo(port, "USB Serial", "USB VID:PID=1A86:7523"),
            FakeListPortInfo("/dev/ttyUSB0", "Linux device"),
            FakeListPortInfo("COM3 evil;rm", "unsafe name"),
        ],
    )

    ports = EspIdfDeviceAdapter().discover_serial_ports()

    assert [item.name for item in ports] == [port]
    assert all(isinstance(item, SerialPortInfo) for item in ports)


@pytest.mark.parametrize(
    "port",
    [
        "/dev/ttyUSB0",
        "COM0",
        "",
        "COM3;rm",
        "\\\\.\\COM3",
        "../COM3",
        "C:COM3",
    ],
)
def test_flash_rejects_invalid_port_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    port: str,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")

    with pytest.raises(EspIdfError) as captured:
        EspIdfDeviceAdapter().flash(project, port)

    assert captured.value.category == "serial"
    # 错误文案是固定脱敏文本，绝不回显用户输入。
    assert "名称无效" in captured.value.message


def test_flash_runs_idf_flash_with_validated_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> FakeCompletedProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeCompletedProcess(0, stdout="Hash of data verified.\n")

    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.run",
        fake_run,
    )

    evidence = EspIdfDeviceAdapter().flash(project, "COM4")

    assert evidence == FlashEvidence(
        success=True,
        command=["idf.py", "-p", "COM4", "flash"],
        return_code=0,
        port="COM4",
        stdout_summary="Hash of data verified.\n",
    )
    assert list(captured["args"][0]) == ["idf.py", "-p", "COM4", "flash"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == project.resolve()
    # 默认禁止依赖解析。
    assert captured["kwargs"]["env"]["IDF_COMPONENT_MANAGER"] == "0"


def test_flash_timeout_becomes_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            ["idf.py", "flash"],
            300,
            output=b"slow",
            stderr=b"C:\\tools\\SECRET\\slow",
        )

    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.run",
        raise_timeout,
    )

    evidence = EspIdfDeviceAdapter().flash(project, "COM4")

    assert evidence.success is False
    assert evidence.return_code == -1
    assert evidence.error_category == "timeout"
    assert "SECRET" not in evidence.stderr_summary


def test_flash_serial_failure_is_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.run",
        lambda *args, **kwargs: FakeCompletedProcess(
            1,
            stderr="A fatal error occurred: Could not open port COM4",
        ),
    )

    evidence = EspIdfDeviceAdapter().flash(project, "COM4")

    assert evidence.success is False
    assert evidence.error_category == "serial"


def test_flash_environment_failure_is_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.run",
        lambda *args, **kwargs: FakeCompletedProcess(
            1,
            stderr="idf_path is not set",
        ),
    )

    evidence = EspIdfDeviceAdapter().flash(project, "COM4")

    assert evidence.success is False
    assert evidence.error_category == "environment"


def test_flash_unknown_failure_is_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.run",
        lambda *args, **kwargs: FakeCompletedProcess(
            2,
            stderr="something unexpected",
        ),
    )

    evidence = EspIdfDeviceAdapter().flash(project, "COM4")

    assert evidence.success is False
    assert evidence.error_category == "unknown"


def test_flash_process_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")

    def raise_oserror(*args: object, **kwargs: object) -> None:
        raise OSError("SECRET_PROCESS_DETAIL")

    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.run",
        raise_oserror,
    )

    with pytest.raises(EspIdfError) as captured:
        EspIdfDeviceAdapter().flash(project, "COM4")

    assert captured.value.category == "process"
    assert "SECRET_PROCESS_DETAIL" not in captured.value.message


def test_flash_rejects_invalid_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)

    with pytest.raises(EspIdfError) as captured:
        EspIdfDeviceAdapter().flash(tmp_path / "missing", "COM4")

    assert captured.value.category == "invalid_project"
