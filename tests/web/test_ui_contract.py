from pathlib import Path

import luxar


def ui_source() -> str:
    return (Path(luxar.__file__).parent / "ui" / "index.html").read_text(
        encoding="utf-8"
    )


def test_migrated_ui_uses_new_workflow_contract() -> None:
    source = ui_source()

    assert "currentEvent === 'progress'" in source
    assert "currentEvent === 'result'" in source
    assert "chunk.message || chunk.error" in source
    assert "max_attempts: 3" in source
    assert "allow_dependency_downloads: false" in source
    assert "docs: docs" not in source


def test_migrated_ui_supports_flash_approval_flow() -> None:
    source = ui_source()

    # SSE 审批事件 + 审批卡片 + 批准/拒绝端点调用。
    assert "currentEvent === 'approval'" in source
    assert "renderApprovalCard" in source
    assert "批准烧录" in source
    assert "'/api/conversations/' + encodeURIComponent(project) + '/approval'" in source
    assert "decide('approve')" in source
    assert "decide('reject')" in source
    assert "JSON.stringify({decision: decision, root_index: selectedRootIndex})" in source


def test_migrated_ui_selects_root_port_and_chip_per_task() -> None:
    source = ui_source()

    # 项目根切换器、串口下拉(来自发现端点)与芯片下拉,随任务提交。
    assert "id=\"serial-port-select\"" in source
    assert "id=\"chip-select\"" in source
    assert "onRootChange" in source
    assert "sidebar-root-select" in source
    assert "apiGet('/api/devices/ports')" in source
    assert "root_index: selectedRootIndex" in source
    assert "serial_port: (portSelect && portSelect.value)" in source
    assert "target_chip: (chipSelect && chipSelect.value)" in source
    assert "root_index=' + selectedRootIndex" in source


def test_migrated_ui_enabled_startup_calls_only_supported_endpoints() -> None:
    source = ui_source()

    assert "var pdata = await apiGet('/api/workspace/projects')" in source
    assert "var r = await fetch('/api/health')" in source
    assert "['drivers', 'skills', 'model-config']" in source
    assert "attachButton.style.display = 'none'" in source
    assert "stopButton.style.display = 'none'" in source
    assert "landing-actions" in source
    assert "请从左侧选择一个已有的 ESP-IDF 项目" in source
    assert "openNewProjectModal = showUnavailableFeature" in source
    assert "loadDrivers = showUnavailableFeature" in source
    assert "saveModelConfig = showUnavailableFeature" in source
