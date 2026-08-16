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
    assert "JSON.stringify({decision: decision})" in source


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
