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
    assert "currentEvent === 'token'" in source
    assert "chunk.message || chunk.error" in source
    assert "max_attempts: 3" in source
    assert "allow_dependency_downloads: false" in source
    assert "docs: docs" not in source
    assert "LangGraph Workflow" not in source
    assert "<h4>任务结果</h4>" in source
    assert "taskResult.status === 'needs_clarification'" in source


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


def test_migrated_ui_uses_project_chip_and_selects_port_per_task() -> None:
    source = ui_source()

    # 芯片只在项目创建/选择时指定；聊天任务只允许选择串口。
    assert "id=\"serial-port-select\"" in source
    assert "id=\"chip-select\"" not in source
    assert "id=\"npm-target\"" in source
    assert "id=\"open-project-target\"" in source
    assert "onRootChange" in source
    assert "sidebar-root-select" in source
    assert "apiGet('/api/devices/ports')" in source
    assert "root_index: selectedRootIndex" in source
    assert "serial_port: (portSelect && portSelect.value)" in source
    assert "target_chip: (chipSelect && chipSelect.value)" not in source
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
    assert 'id="sidebar-new-project"' in source
    assert 'id="sidebar-select-project"' in source
    assert "sidebar.newProject" in source
    assert "sidebar.selectProject" in source
    assert "fetch('/api/workspace/projects', { method: 'POST'" in source
    assert 'id="toolchain-warning"' in source
    assert 'id="toolchain-select-btn"' in source
    assert "apiGet('/api/toolchains/espidf')" in source
    assert "fetch('/api/toolchains/espidf/select-directory', {method:'POST'})" in source
    assert "if (!espIdfToolchain || !espIdfToolchain.available)" in source
    assert "openNewProjectModal = showUnavailableFeature" not in source
    assert "openOpenProjectModal = showUnavailableFeature" not in source
    assert "deleteProject = showUnavailableFeature" not in source
    assert "loadDrivers = showUnavailableFeature" in source
    assert "saveModelConfig = showUnavailableFeature" in source


def test_project_selector_uses_native_directory_endpoint() -> None:
    source = ui_source()

    assert "fetch('/api/workspace/projects/select-directory', {" in source
    assert "JSON.stringify({target_chip: document.getElementById('open-project-target').value})" in source
    assert "selectedRootIndex = data.project.root_index" in source
    assert "projectDirectorySelectionInProgress" in source
    assert 'id="open-project-modal"' in source
    assert "projectMetaByName[p.root_index + ':' + p.name]" in source
    assert "root_index: selectedRootIndex" in source


def test_project_rows_show_a_hover_delete_action() -> None:
    source = ui_source()

    assert 'class="proj-del"' in source
    assert ".sidebar-project:hover .proj-del" in source
    assert "event.stopPropagation();deleteProject" in source
    assert "method: 'DELETE'" in source
    assert "?root_index=' + selectedRootIndex" in source
