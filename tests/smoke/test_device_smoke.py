"""真实设备冒烟测试：项目创建 → 真实构建 → 真实烧录 → 受控监控采集。

仅在 LUXAR_RUN_ESPIDF_SMOKE=1 且 LUXAR_ESP32_PORT 已设置时运行。
测试在 pytest 临时目录中创建依赖自由的工程，烧录会替换开发板上的
现有固件，因此默认绝不执行。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from luxar.adapters.espidf_cli import EspIdfCliAdapter
from luxar.adapters.espidf_device import EspIdfDeviceAdapter
from luxar.adapters.espidf_project import EspIdfProjectAdapter

# 本机已知的 ESP-IDF v6.0.2 安装（由 Espressif IDE 安装器记录）。
_IDF_VENV_PYTHON = (
    r"F:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe"
)
_IDF_SCRIPT = r"F:\esp\v6.0.2\esp-idf\tools\idf.py"
_IDF_PATH = r"F:\esp\v6.0.2\esp-idf"
_IDF_TOOLS_PATH = r"F:\Espressif\tools"


def _resolve_launcher() -> tuple[str, ...]:
    """优先使用本机已知的 venv python + idf.py 脚本。

    激活脚本把 idf.py 注册成 PowerShell 函数而不是可执行文件，
    直接 spawn "idf.py" 会以 WinError 193 失败，因此已知安装优先。
    """

    if Path(_IDF_VENV_PYTHON).is_file() and Path(_IDF_SCRIPT).is_file():
        return (_IDF_VENV_PYTHON, _IDF_SCRIPT)

    if shutil.which("idf.py") is not None:
        return ("idf.py",)

    pytest.skip("idf.py is not available in the active environment")
    raise AssertionError("unreachable")


def _require_smoke(monkeypatch: pytest.MonkeyPatch) -> str:
    if os.environ.get("LUXAR_RUN_ESPIDF_SMOKE") != "1":
        pytest.skip(
            "set LUXAR_RUN_ESPIDF_SMOKE=1 to run the real device smoke"
        )

    port = os.environ.get("LUXAR_ESP32_PORT", "").strip()
    if not port:
        pytest.skip("set LUXAR_ESP32_PORT to the board serial port")

    # 让 idf.py 子进程能找到框架、工具链与专用 Python 环境。
    if Path(_IDF_PATH).is_dir():
        monkeypatch.setenv("IDF_PATH", _IDF_PATH)
        monkeypatch.setenv("IDF_TOOLS_PATH", _IDF_TOOLS_PATH)
        monkeypatch.setenv(
            "IDF_PYTHON_ENV_PATH",
            str(Path(_IDF_VENV_PYTHON).parent.parent),
        )
        monkeypatch.setenv("ESP_IDF_VERSION", "6.0.2")

    # 中文 Windows 默认 GBK 编码会让 idf_monitor 在写引导日志时崩溃，
    # 强制整个 idf.py 子进程树使用 UTF-8。
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("PYTHONUTF8", "1")

    return port


def test_real_flash_and_monitor_smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _require_smoke(monkeypatch)
    launcher = _resolve_launcher()
    target = os.environ.get("LUXAR_ESP32_TARGET", "esp32")

    parent = tmp_path / "parent"
    parent.mkdir()

    # 1. 项目创建：真实 idf.py create-project。
    creation = EspIdfProjectAdapter(
        idf_command=launcher,
        create_timeout_seconds=300,
    ).create_project(parent, "smoke", target)
    assert creation.success is True
    assert creation.created_dir == "smoke"
    assert creation.already_existed is False

    project = parent / "smoke"
    assert (project / "CMakeLists.txt").is_file()
    config = (project / "sdkconfig.defaults").read_text(
        encoding="utf-8"
    )
    assert f"CONFIG_IDF_TARGET={target}" in config

    # 2. 真实构建（依赖下载保持禁止）。
    build_evidence = EspIdfCliAdapter(
        idf_command=launcher,
        allow_dependency_downloads=False,
        reconfigure_timeout_seconds=300,
        build_timeout_seconds=1200,
    ).build(project)
    assert build_evidence.success is True
    assert build_evidence.error_category is None

    device = EspIdfDeviceAdapter(idf_command=launcher)

    # 3. 串口发现必须包含开发板。
    discovered = [item.name for item in device.discover_serial_ports()]
    assert port in discovered

    # 4-5. 单进程连续烧录并监控，避免两个进程抢占同一串口。
    flash_evidence, monitor_evidence = device.flash_and_monitor(
        project,
        port,
        15,
    )
    assert flash_evidence.success is True
    assert flash_evidence.error_category is None
    assert flash_evidence.port == port
    assert monitor_evidence.port == port
    assert monitor_evidence.terminated_by_timeout is True
    # 空模板不打印业务输出，但复位后必然有引导/复位日志。
    assert monitor_evidence.captured_log.strip()
