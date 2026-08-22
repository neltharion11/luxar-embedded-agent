from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_script_has_no_machine_specific_espidf_path() -> None:
    source = (ROOT / "start.ps1").read_text(encoding="utf-8-sig")

    assert r"F:\esp" not in source
    assert r"F:\Espressif" not in source
    assert "自动检测 ESP-IDF 工具链" in source
    assert "from langgraph.checkpoint.sqlite import SqliteSaver" in source
    assert "import lancedb" in source
    assert "LUXAR_STORAGE_DIRECTORY" in source
    assert "Join-Path $root '.luxar-data'" in source


def test_setup_script_uses_shared_python_toolchain_detector() -> None:
    source = (ROOT / "scripts" / "setup.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert r"F:\esp" not in source
    assert r"F:\Espressif" not in source
    assert "EspIdfToolchainManager" in source
    assert "仪表盘选择工具链位置" in source
    assert "from langgraph.checkpoint.sqlite import SqliteSaver" in source
    assert "import lancedb" in source
