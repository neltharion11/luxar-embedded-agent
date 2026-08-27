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


class FakeMonitorProcess:
    pid = 4242

    def __init__(
        self,
        stdout_text: str = "",
        stderr_text: str = "",
        *,
        timeout_on_first_communicate: bool,
    ) -> None:
        self.stdout_text = stdout_text
        self.stderr_text = stderr_text
        self.timeout_on_first_communicate = timeout_on_first_communicate
        self.terminated = False
        self.killed = False
        self.communicate_timeouts: list[float | None] = []

    def communicate(
        self,
        timeout: float | None = None,
    ) -> tuple[str, str]:
        self.communicate_timeouts.append(timeout)
        if timeout is not None and self.timeout_on_first_communicate:
            self.timeout_on_first_communicate = False
            raise subprocess.TimeoutExpired(["idf.py", "monitor"], timeout)
        return self.stdout_text, self.stderr_text

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


def _patch_popen(
    monkeypatch: pytest.MonkeyPatch,
    process: FakeMonitorProcess,
) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_popen(*args: object, **kwargs: object) -> FakeMonitorProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.Popen",
        fake_popen,
    )
    return captured


def test_monitor_captures_window_and_terminates_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    process = FakeMonitorProcess(
        stdout_text=(
            "rst:0x1 (POWERON_RESET)\n"
            "Guru Meditation Error: Core 0 panic'ed\n"
            "C:\\tools\\secret-path\n"
        ),
        timeout_on_first_communicate=True,
    )
    captured = _patch_popen(monkeypatch, process)
    taskkill_calls: list[object] = []

    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.run",
        lambda *args, **kwargs: taskkill_calls.append(args),
    )

    evidence = EspIdfDeviceAdapter().monitor(project, "COM4", 10)

    assert evidence.terminated_by_timeout is True
    assert evidence.capture_timeout_seconds == 10
    assert evidence.port == "COM4"
    assert "secret-path" not in evidence.captured_log
    assert "Guru Meditation Error" in evidence.captured_log
    kinds = [diagnostic.kind for diagnostic in evidence.diagnostics]
    assert "panic" in kinds
    assert list(captured["args"][0]) == [
        "idf.py",
        "-p",
        "COM4",
        "monitor",
    ]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == project.resolve()
    # 超时后清理了整个进程树。
    assert taskkill_calls
    assert "4242" in str(taskkill_calls[0])


def test_monitor_self_exit_is_not_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    process = FakeMonitorProcess(
        stdout_text="boot ok\n",
        timeout_on_first_communicate=False,
    )
    _patch_popen(monkeypatch, process)

    evidence = EspIdfDeviceAdapter().monitor(project, "COM4", 10)

    assert evidence.terminated_by_timeout is False
    assert "boot ok" in evidence.captured_log


def test_flash_and_monitor_runs_bounded_flash_then_short_monitor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    process = FakeMonitorProcess(
        stdout_text="I (0) app_main: boot ok\n",
        timeout_on_first_communicate=False,
    )
    captured = _patch_popen(monkeypatch, process)
    flash_calls: list[tuple[object, ...]] = []

    def fake_run(*args: object, **kwargs: object) -> FakeCompletedProcess:
        flash_calls.append(args)
        return FakeCompletedProcess(
            0,
            stdout="Hash of data verified.\nHard resetting via RTS pin...\n",
        )

    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.run",
        fake_run,
    )

    flash, monitor = EspIdfDeviceAdapter().flash_and_monitor(
        project,
        "COM4",
        12,
    )

    assert flash.success is True
    assert flash.command == ["idf.py", "-p", "COM4", "flash"]
    assert monitor.terminated_by_timeout is False
    assert "boot ok" in monitor.captured_log
    assert list(flash_calls[0][0]) == ["idf.py", "-p", "COM4", "flash"]
    assert list(captured["args"][0]) == ["idf.py", "-p", "COM4", "monitor"]
    assert process.communicate_timeouts == [12]


def test_flash_and_monitor_accepts_flash_success_when_monitor_times_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    process = FakeMonitorProcess(
        stdout_text=(
            "Hash of data verified.\n"
            "Hard resetting via RTS pin...\n"
            "I (0) app_main: boot ok\n"
        ),
        timeout_on_first_communicate=True,
    )
    _patch_popen(monkeypatch, process)

    def fake_run(*args: object, **kwargs: object) -> FakeCompletedProcess:
        command = list(args[0])
        if command[0] == "taskkill":
            return FakeCompletedProcess(0)
        return FakeCompletedProcess(
            0,
            stdout="Hash of data verified.\nHard resetting via RTS pin...\n",
        )

    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.run",
        fake_run,
    )

    flash, monitor = EspIdfDeviceAdapter().flash_and_monitor(
        project,
        "COM4",
        12,
    )

    assert flash.success is True
    assert flash.return_code == 0
    assert monitor.terminated_by_timeout is True
    assert "boot ok" in monitor.captured_log


def test_flash_and_monitor_does_not_monitor_after_flash_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    popen_called = False

    def fake_popen(*args: object, **kwargs: object) -> FakeMonitorProcess:
        nonlocal popen_called
        popen_called = True
        return FakeMonitorProcess(timeout_on_first_communicate=False)

    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.Popen",
        fake_popen,
    )
    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.run",
        lambda *args, **kwargs: FakeCompletedProcess(
            2,
            stderr="Failed to connect to ESP32",
        ),
    )

    flash, monitor = EspIdfDeviceAdapter().flash_and_monitor(
        project,
        "COM4",
        12,
    )

    assert flash.success is False
    assert flash.error_category == "serial"
    assert monitor.captured_log == ""
    assert popen_called is False


def test_monitor_rejects_invalid_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")

    with pytest.raises(EspIdfError) as captured:
        EspIdfDeviceAdapter().monitor(project, "COM3;rm", 10)

    assert captured.value.category == "serial"


def test_monitor_rejects_invalid_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")

    with pytest.raises(ValueError, match="timeout_seconds"):
        EspIdfDeviceAdapter().monitor(project, "COM4", 0)


def test_monitor_process_spawn_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")

    def raise_oserror(*args: object, **kwargs: object) -> None:
        raise OSError("SECRET_PROCESS_DETAIL")

    monkeypatch.setattr(
        "luxar.adapters.espidf_device.subprocess.Popen",
        raise_oserror,
    )

    with pytest.raises(EspIdfError) as captured:
        EspIdfDeviceAdapter().monitor(project, "COM4", 10)

    assert captured.value.category == "process"
    assert "SECRET_PROCESS_DETAIL" not in captured.value.message


@pytest.mark.parametrize(
    ("log_text", "expected_kind"),
    [
        ("Guru Meditation Error: Core 0 panic'ed", "panic"),
        ("abort() was called on CPU0", "abort"),
        ("assert failed: xQueueSemaphoreTake", "assert"),
        ("task_wdt: Task watchdog got triggered", "watchdog"),
        ("I (0) cpu_start: starting\nBacktrace: 0x4001", "unknown"),
        ("E (1234) gpio: failed to init", "error"),
        ("W (1234) wifi: weak signal", "warning"),
    ],
)
def test_parse_device_diagnostics_patterns(
    log_text: str,
    expected_kind: str,
) -> None:
    from luxar.adapters.espidf_device import _parse_device_diagnostics

    diagnostics = _parse_device_diagnostics(log_text)

    assert [diagnostic.kind for diagnostic in diagnostics] == [
        expected_kind
    ]


def test_parse_device_diagnostics_ignores_armed_watchdog_health_log() -> None:
    from luxar.adapters.espidf_device import _parse_device_diagnostics

    diagnostics = _parse_device_diagnostics(
        "I (100) diagnostics: status=ok errors=0 watchdog=armed"
    )

    assert diagnostics == []


def test_parse_device_diagnostics_detects_boot_loop() -> None:
    from luxar.adapters.espidf_device import _parse_device_diagnostics

    diagnostics = _parse_device_diagnostics(
        "rst:0x1 (POWERON_RESET)\n"
        "boot: starting\n"
        "rst:0x1 (POWERON_RESET)\n"
        "boot: starting\n"
    )

    assert [diagnostic.kind for diagnostic in diagnostics] == [
        "boot_loop"
    ]


def test_parse_device_diagnostics_ignores_corrupted_duplicate_reset_line() -> None:
    from luxar.adapters.espidf_device import _parse_device_diagnostics

    diagnostics = _parse_device_diagnostics(
        "rst:0x1 (POWERON_RESET),boot:\ufffdets Jul 29 2019\n"
        "rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)\n"
        "I (286) diagnostics: status=ok errors=0 watchdog=armed\n"
    )

    assert all(diagnostic.kind != "boot_loop" for diagnostic in diagnostics)


def test_parse_device_diagnostics_bounds_error_count() -> None:
    from luxar.adapters.espidf_device import _parse_device_diagnostics

    log = "\n\n".join(f"E (1{i}) comp: message {i}" for i in range(12))
    diagnostics = _parse_device_diagnostics(log)

    assert len(diagnostics) == 5
    assert all(diagnostic.kind == "error" for diagnostic in diagnostics)
