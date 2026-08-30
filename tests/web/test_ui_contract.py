from pathlib import Path

import luxar


def ui_source() -> str:
    return (Path(luxar.__file__).parent / "ui" / "index.html").read_text(
        encoding="utf-8"
    )


def test_migrated_ui_uses_new_workflow_contract() -> None:
    source = ui_source()

    assert "currentEvent === 'progress'" in source
    assert "Array.isArray(data.tools)" in source
    assert "tools.map(toolDisplayName).join('、')" in source
    assert "currentEvent === 'result'" in source
    assert "currentEvent === 'token'" in source
    assert "currentEvent === 'turn_status'" in source
    assert "eventName === 'turn_status'" in source
    assert "chunk.message || chunk.error" in source
    assert "max_attempts: 3" in source
    assert "allow_dependency_downloads: false" in source
    assert "session_id: activeAgentSessions[runKey] || null" in source
    assert "client_turn_id: createClientTurnId()" in source
    assert "X-LUXAR-Session-ID" in source
    assert "taskResult.domain_calls" in source
    assert "taskResult.evidence_ids" in source
    assert "currentEvent === 'tool_result'" in source
    assert "docs: docs" not in source
    assert "LangGraph Workflow" not in source
    assert "<h4>任务结果</h4>" in source
    assert "taskResult.status === 'needs_clarification'" in source
    assert 'id="dash-chat-context-window"' in source
    assert "context_window_tokens" in source
    assert "达到窗口的 95% 时自动压缩" in source


def test_chat_recovers_in_progress_output_after_page_reload() -> None:
    source = ui_source()

    assert "data.active_run" in source
    assert "activeRun.assistant_content" in source
    assert "recoverConversationRun" in source
    assert "after_sequence=" in source
    assert "X-LUXAR-Thread-ID" in source
    assert "applyConversationStreamEvent" in source
    assert "recoveryControllers" in source
    assert "当前任务仍在运行，无法清空对话" in source


def test_continuous_agent_ui_supports_runtime_steering_and_safe_cancel() -> None:
    source = ui_source()

    assert "continuousAgentV2Enabled" in source
    assert "'/steer'" in source
    assert "client_steering_id: createClientTurnId()" in source
    assert "'/cancel'" in source
    assert "session_id: runningSessionId" in source
    assert "stopBtn.style.display = busy && canControl ? '' : 'none'" in source
    assert "activeConversationRuns[runKey] = {threadId: null, mode: 'primary'}" in source


def test_chat_renders_and_restores_pdf_page_progress() -> None:
    source = ui_source()

    assert "createTaskProgressController" in source
    assert "data.progress_type !== 'pdf'" in source
    assert "current + ' / ' + total + ' 页 · ' + percent + '%'" in source
    assert "activeRun.progress" in source
    assert 'role="progressbar"' in source
    assert ".task-progress-fill" in source


def test_chat_shows_collapsed_auditable_run_details() -> None:
    source = ui_source()

    assert "createReasoningSummaryController" in source
    assert "reasoning-summary" in source
    assert "view.reasoningController.update(data, eventName)" in source
    assert "reasoningController.update(progressChunk, 'progress')" in source
    assert "运行详情" in source
    assert "不包含模型私密逐字思维链" in source
    assert "document.createElement('details')" in source


def test_chat_shows_live_semantic_activity_without_heartbeat_masking_stalls() -> None:
    source = ui_source()

    assert "eventName === 'commentary'" in source
    assert "reasoningController.update(data, eventName)" in source
    assert "createCommentaryController" in source
    assert "commentaryController.update(commentaryChunk)" in source
    assert "view.commentaryController.update(data)" in source
    assert "reasoningController.heartbeat(data)" in source
    assert "statusController.markHeartbeat()" in source
    assert ".commentary-message{" in source


def test_chat_surface_failures_as_expandable_error_cards() -> None:
    """工具失败/模型决策失败/审批拒绝必须在前端显式呈现为可排查的错误卡片。"""
    source = ui_source()

    assert "function renderFailureCard" in source
    assert "function appendFailureCard" in source
    assert "data-failure-card" in source
    # 样式
    assert ".failure-card{" in source or ".failure-card{" in source.replace(" ", "")
    assert ".failure-code{" in source
    assert ".failure-details pre" in source
    # 可展开的错误详情（排查依据）
    assert "错误详情（排查依据）" in source
    assert "JSON.stringify(details, null, 2)" in source
    # 恢复路径：tool_result 失败/拒绝 -> 卡片
    assert "data.status === 'failed' || data.status === 'rejected'" in source
    assert "appendFailureCard(view.bubbleDiv, data.failure" in source
    # 恢复路径：error 事件结构化
    assert "data && (data.code || data.category || data.message)" in source
    # 主流路径：tool_result 失败/拒绝 -> 卡片
    assert "toolChunk.status === 'failed' || toolChunk.status === 'rejected'" in source
    assert "appendFailureCard(bubbleDiv, toolChunk.failure" in source
    # 主流路径：error 事件结构化
    assert "chunk && (chunk.code || chunk.category || chunk.message)" in source
    # turn 结果卡片内嵌结构化失败
    assert "renderFailureCard(continuousFailure, {})" in source
    # 分类与错误码展示
    assert "failureCategoryLabel" in source
    assert "failure-code" in source


def test_chat_heartbeat_preserves_the_active_tool_name() -> None:
    source = ui_source()

    assert "function setToolRunningFromProgress" in source
    assert "data.phase === 'heartbeat'" in source
    assert "statusController.setState('tool_running');" in source
    assert "setToolRunningFromProgress(view.statusController, data)" in source
    assert "setToolRunningFromProgress(statusController, progressChunk)" in source


def test_migrated_ui_supports_flash_approval_flow() -> None:
    source = ui_source()

    # SSE 审批事件 + 审批卡片 + 批准/拒绝端点调用。
    assert "currentEvent === 'approval'" in source
    assert "renderApprovalCard" in source
    assert "批准烧录" in source
    assert "批准该任务并继续" in source
    assert "request.task_description" in source
    assert "request.planned_actions" in source
    assert "request.tools" in source
    assert "request.affected_targets" in source
    assert "request.acceptance_criteria" in source
    assert "request.preserve_conditions" in source
    assert "request.risks" in source
    assert "批准后将依次执行" in source
    assert "完成前必须通过" in source
    assert "主要风险" in source
    assert "'/api/conversations/' + encodeURIComponent(project) + '/approval'" in source
    assert "decide('approve')" in source
    assert "decide('reject')" in source
    assert "JSON.stringify({decision: decision, feedback: feedback, root_index: selectedRootIndex})" in source
    assert "payload.status === 'resuming'" in source
    assert "resumeConversationRunAfterApproval(project, selectedRootIndex, bubbleDiv)" in source
    assert "bubbleDiv.__luxarStreamView" in source


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

    assert "apiGet('/api/workspace/projects')" in source
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
    assert "saveDashboardModelConfig" in source
    assert "apiGet('/api/config/models')" in source
    assert "fetch('/api/config/models'" in source
    assert 'id="dash-chat-model"' in source
    assert 'dash-chat-repair-model' not in source
    assert "if (force) model.value = defaults.model" in source
    assert 'id="dash-embedding-mode"' in source
    assert "dashboardEmbeddingBody" in source
    assert "本地 Hash（离线）" in source
    assert "独立 Embedding API" in source


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


def test_agent_workspace_uses_safe_snapshot_and_interaction_apis() -> None:
    source = ui_source()

    assert 'data-page-link="agent"' in source
    assert 'id="agent-workspace"' in source
    assert "else if (page === 'agent') {" in source
    assert "loadAgentWorkspace(false, true)" in source
    assert "agentSnapshotPoll = setInterval" in source
    assert "'/agent?root_index=' + rootIndex" in source
    assert "'/agent/interactions'" in source
    assert "kind: kind.value" in source
    assert '<option value="question">' in source
    assert '<option value="change_objective">' in source
    assert '<option value="change_plan">' in source
    assert "escapeHtml(data.blocked_reason)" in source
    assert "escapeHtml(item.evidence_id)" in source
    assert "escapeHtml(item.message)" in source
    assert "task.allowed_paths" not in source
    assert "item.source_paths" not in source
    assert "data.task_mode === 'knowledge'" in source
    assert "data.knowledge_task || {}" in source
    assert "data.knowledge_result || {}" in source
    assert "data.supports_interactions === false" in source


def test_serial_terminal_is_a_fourth_primary_page_with_safe_session_api() -> None:
    source = ui_source()

    assert 'data-page-link="serial"' in source
    assert 'data-page="serial"' in source
    assert 'id="serial-tool-port"' in source
    assert 'id="serial-tool-console"' in source
    assert 'id="serial-send-mode"' in source
    assert '<option value="hex">HEX</option>' in source
    assert "apiGet('/api/devices/ports')" in source
    assert "fetch('/api/serial/sessions'" in source
    assert "'/write'" in source
    assert "method: 'DELETE'" in source
    assert "line.textContent =" in source
    assert "serialTerminalEvents = serialTerminalEvents.concat(events).slice(-1000)" in source


def test_dashboard_driver_count_uses_the_public_driver_library() -> None:
    source = ui_source()

    assert "apiGet('/api/drivers?limit=100')" in source
    assert "Number(driverData.count || 0)" in source
