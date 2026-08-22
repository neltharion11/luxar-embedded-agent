from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from luxar.toolchain import EspIdfToolchainManager


def _idf_root(root: Path) -> Path:
    idf = root / "esp-idf"
    (idf / "tools").mkdir(parents=True)
    (idf / "tools" / "idf.py").write_text("# idf.py\n", encoding="utf-8")
    return idf


def _clear_detection_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IDF_PATH", raising=False)
    monkeypatch.delenv("IDF_PYTHON_ENV_PATH", raising=False)
    monkeypatch.delenv("IDF_TOOLS_PATH", raising=False)
    monkeypatch.delenv("ESP_IDF_VERSION", raising=False)
    monkeypatch.setattr("luxar.toolchain.shutil.which", lambda _: None)


def test_manager_reports_missing_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_detection_environment(monkeypatch)

    manager = EspIdfToolchainManager(
        config_path=tmp_path / "toolchain.json",
        installer_config_paths=[],
        idf_search_paths=[],
    )

    assert manager.command is None
    assert manager.status.available is False
    assert manager.status.source == "none"
    assert manager.status.message == "未搜索到可用的 ESP-IDF 环境"


def test_manager_detects_environment_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_detection_environment(monkeypatch)
    idf_path = _idf_root(tmp_path)
    monkeypatch.setenv("IDF_PATH", str(idf_path))
    calls: list[tuple[tuple[str, ...], Path | None]] = []

    def probe(command, root):
        calls.append((tuple(command), root))
        return True, "ESP-IDF v6.0.2"

    manager = EspIdfToolchainManager(
        config_path=tmp_path / "toolchain.json",
        probe=probe,
        installer_config_paths=[],
        idf_search_paths=[],
    )

    assert manager.status.available is True
    assert manager.status.source == "environment"
    assert manager.status.version == "ESP-IDF v6.0.2"
    assert manager.status.idf_path == str(idf_path.resolve())
    assert manager.command is not None
    assert calls[0][1] == idf_path.resolve()


def test_manual_configuration_is_persisted_and_reloaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_detection_environment(monkeypatch)
    idf_path = _idf_root(tmp_path)
    config = tmp_path / "state" / "toolchain.json"

    def probe(command, root):
        return True, "ESP-IDF v5.4.1"

    manager = EspIdfToolchainManager(
        config_path=config,
        probe=probe,
        installer_config_paths=[],
        idf_search_paths=[],
    )
    selected = manager.configure(idf_path)
    reloaded = EspIdfToolchainManager(
        config_path=config,
        probe=probe,
        installer_config_paths=[],
        idf_search_paths=[],
    )

    assert selected.available is True
    assert selected.source == "configured"
    assert json.loads(config.read_text(encoding="utf-8")) == {
        "idf_path": str(idf_path.resolve())
    }
    assert reloaded.status.available is True
    assert reloaded.status.source == "configured"
    assert reloaded.command == manager.command


def test_manual_configuration_reports_incomplete_python_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_detection_environment(monkeypatch)
    idf_path = _idf_root(tmp_path)
    manager = EspIdfToolchainManager(
        config_path=tmp_path / "toolchain.json",
        probe=lambda command, root: (False, "missing module"),
        installer_config_paths=[],
        idf_search_paths=[],
    )

    status = manager.configure(idf_path)

    assert status.available is False
    assert status.source == "configured"
    assert status.idf_path == str(idf_path.resolve())
    assert status.message == "检测到 ESP-IDF 目录，但其 Python 工具环境不可用"


def test_manual_configuration_rejects_non_idf_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_detection_environment(monkeypatch)
    manager = EspIdfToolchainManager(
        config_path=tmp_path / "toolchain.json",
        installer_config_paths=[],
        idf_search_paths=[],
    )

    with pytest.raises(ValueError, match="所选目录不是 ESP-IDF 根目录"):
        manager.configure(tmp_path)


def test_manager_detects_espressif_installer_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_detection_environment(monkeypatch)
    idf_path = _idf_root(tmp_path)
    python = tmp_path / "tools" / "python" / "v6.0.2" / "venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    installer_config = tmp_path / "eim_idf.json"
    installer_config.write_text(
        json.dumps(
            {
                "idfSelectedId": "selected",
                "idfInstalled": [
                    {
                        "id": "selected",
                        "name": "v6.0.2",
                        "path": str(idf_path),
                        "python": str(python),
                        "idfToolsPath": str(tmp_path / "tools"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def probe(command, root):
        assert Path(command[0]) == python
        assert root == idf_path.resolve()
        assert os.environ["IDF_TOOLS_PATH"] == str(tmp_path / "tools")
        assert os.environ["ESP_IDF_VERSION"] == "6.0"
        return True, "ESP-IDF v6.0.2"

    manager = EspIdfToolchainManager(
        config_path=tmp_path / "toolchain.json",
        probe=probe,
        installer_config_paths=[installer_config],
        idf_search_paths=[],
    )

    assert manager.status.available is True
    assert manager.status.source == "installer"
    assert manager.status.version == "ESP-IDF v6.0.2"


def test_manager_detects_standard_search_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_detection_environment(monkeypatch)
    idf_path = _idf_root(tmp_path)
    manager = EspIdfToolchainManager(
        config_path=tmp_path / "toolchain.json",
        probe=lambda command, root: (True, "ESP-IDF v5.3.2"),
        installer_config_paths=[],
        idf_search_paths=[idf_path],
    )

    assert manager.status.available is True
    assert manager.status.source == "search"
    assert manager.status.idf_path == str(idf_path.resolve())
