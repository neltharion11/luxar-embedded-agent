"""LUXAR Web 展示入口：用安全 HTTP/SSE 合同调用现有 Bootstrap 与 Runner。

项目根与芯片在创建或选择项目时固定；后续任务只允许选择串口。
项目根必须在服务器配置的根列表内，串口必须通过平台模式校验并
出现在服务器实时发现的列表里。任意值永远不会直接到达 idf.py。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from collections.abc import Callable, Generator, Sequence
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from luxar import arguments
from luxar.agent_status import AgentStatusTool
from luxar.application.agent_results import (
    agent_state_to_result,
    agent_user_message_for_state,
)
from luxar.application.agent_persistence import load_agent_snapshot
from luxar.application.agent_runner import (
    AgentWorkflowProgress,
    AgentWorkflowRunResult,
    resume_agent_workflow,
    run_agent_workflow,
)
from luxar.application.agent_state import AgentRuntimeContext
from luxar.application.context import RuntimeContext
from luxar.application.specialized_context import SpecializedRuntimeContext
from luxar.application.specialized_results import (
    specialized_state_to_result,
    specialized_user_message,
)
from luxar.application.specialized_runner import (
    SpecializedWorkflowRunResult,
    resume_specialized_workflow,
    run_specialized_workflow,
)
from luxar.application.specialized_state import SpecializedWorkflowState
from luxar.application.workbench_persistence import (
    knowledge_workbench_snapshot,
)
from luxar.application.results import (
    live_message_for_state,
    state_to_result,
    user_message_for_state,
)
from luxar.application.legacy_retirement import current_legacy_retirement
from luxar.application.continuous_agent_mode import select_continuous_agent
from luxar.application.continuous_agent_rollout import (
    select_continuous_agent_rollout,
)
from luxar.application.continuous_agent_identity import (
    ContinuousAgentTurnIdentity,
    begin_continuous_agent_turn,
)
from luxar.application.continuous_agent_graph import ContinuousAgentRuntimeContext
from luxar.application.domain_workflow_registry import DomainWorkflowRegistry
from luxar.application.continuous_agent_steering import (
    ContinuousAgentSteeringQueue,
    SteeringMessage,
)
from luxar.application.continuous_agent_shadow import (
    compare_shadow_decision,
    summarize_shadow_decisions,
)
from luxar.domain.continuous_agent.events import ConversationEvent
from luxar.domain.continuous_agent.steps import AgentStepContext
from luxar.adapters.continuous_agent_tools import create_core_tool_registry
from luxar.adapters.deepseek.continuous_agent_step import (
    DeepSeekContinuousAgentStep,
)
from luxar.adapters.deepseek.client import OpenAICompatibleJsonClient
from luxar.adapters.espidf_cli import EspIdfCliAdapter
from luxar.adapters.espidf_device import EspIdfDeviceAdapter
from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.adapters.transactional_code_executor import LocalChangeBundleExecutor
from luxar.adapters.project_change_workflow import ProjectChangeWorkflow
from luxar.web_continuous_agent import (
    resume_continuous_agent_http_approval,
    run_continuous_agent_http_turn,
    wait_for_continuous_agent_workers,
)
from luxar.checkpoint_serde import create_checkpoint_serializer
from luxar.application.runtime_dispatch import (
    TaskExecutionMode,
    dispatch_runtime,
)
from luxar.application.runtime_observation import (
    audit_runtime_retirement,
    inspect_sqlite_checkpoint_threads,
)
from luxar.application.runner import (
    WorkflowProgress,
    WorkflowRunResult,
    resume_workflow,
    run_workflow,
)
from luxar.application.runtime_mode import (
    AgentRuntimeMode,
    select_firmware_runtime,
)
from luxar.application.runtime_qualification import (
    current_supervisor_qualification,
)
from luxar.application.state import WorkflowState
from luxar.bootstrap import (
    build_deepseek_agent_runtime_context,
    build_deepseek_runtime_context,
    build_deepseek_specialized_runtime_context,
    discover_serial_ports,
)
from luxar.adapters.deepseek.conversation_router import (
    DeepSeekConversationRouter,
)
from luxar.adapters.deepseek.client import DeepSeekJsonClient
from luxar.adapters.deepseek.knowledge_extractor import DeepSeekKnowledgeAtomExtractor
from luxar.domain.conversation import (
    ConversationDecision,
    is_display_diagnosis_request,
    is_explicit_firmware_command,
    is_explicit_pdf_read_request,
)
from luxar.domain.devices import SerialPortInfo
from luxar.adapters.espidf_project import EspIdfProjectAdapter
from luxar.database import (
    LocalStorageRuntime,
    LocalStorageSettings,
    PendingApprovalRecord,
    PersistencePort,
    TransientPersistence,
)
from luxar.database.persistence import WorkbenchSnapshotRecord
from luxar.lance_knowledge import LanceDBKnowledgeIndex
from luxar.knowledge import (
    KnowledgeService,
    KnowledgeSettings,
    LocalHashEmbeddingAdapter,
    OpenAIEmbeddingAdapter,
)
from luxar.document_reader import PdfDocumentReader, configured_drawing_analyzer
from luxar.model_config import (
    EmbeddingConfig,
    ModelConfigStore,
    ModelEndpoint,
    RuntimeModelConfig,
)
from luxar.sdk_knowledge import SdkExampleKnowledgeBase
from luxar.ports.espidf_errors import EspIdfError
from luxar.ports.espidf_project import EspIdfProjectPort
from luxar.ports.conversation_router import ConversationRouter
from luxar.ports.errors import CapabilityError
from luxar.toolchain import EspIdfToolchainManager, EspIdfToolchainStatus
from luxar.web_contracts import (
    WebAgentCapability,
    WebAgentEvidence,
    WebAgentInteraction,
    WebAgentInteractionRequest,
    WebAgentObjective,
    WebAgentSnapshot,
    WebAgentTask,
    WebApprovalDecision,
    WebHealth,
    WebEspIdfToolchain,
    WebProject,
    WebProjectList,
    WebProjectSelection,
    WebProjectSelectionRequest,
    WebProjectCreateRequest,
    WebProjectRoot,
    WebSerialPort,
    WebSerialPortList,
    WebTaskRequest,
    WebSteeringRequest,
    WebCancelRequest,
    WebMemoryUpsert,
    WebKnowledgeIngest,
    WebKnowledgeSearch,
    WebKnowledgePdfImport,
    WebModelConfigUpdate,
    WebModelEndpointUpdate,
    WebEmbeddingUpdate,
)
from luxar.web_agent import agent_snapshot_contract, workbench_snapshot_contract
from luxar.web_projects import WebProjectCatalog, WebProjectError


logger = logging.getLogger(__name__)

_WORKFLOW_HEARTBEAT_SECONDS = 15.0


_CONVERSATION_SUMMARY_KEY = "conversation.context_summary"
_RETRY_DIRECTIVE = re.compile(
    r"^(?:请)?(?:重试|继续|接着(?:做|处理|来)?|再试(?:一次)?|重新执行|retry|continue)"
    r"[\s。.!！]*$",
    re.IGNORECASE,
)


def _is_retry_directive(message: str) -> bool:
    return bool(_RETRY_DIRECTIVE.fullmatch(message.strip()))


def _run_context(record: object | None) -> dict[str, object] | None:
    if record is None:
        return None
    result = getattr(record, "result", None)
    if not isinstance(result, dict):
        return None
    return {
        **result,
        "task_text": str(getattr(record, "task_text", "")),
        "status": str(getattr(record, "status", "failed")),
    }


def _retry_task_text(previous: dict[str, object]) -> str:
    original = str(previous.get("task_text", "")).strip()
    retry_prefix = "继续并重试上一任务："
    if original.startswith(retry_prefix):
        original = original.splitlines()[0].removeprefix(retry_prefix).strip()
    error = str(previous.get("last_error", "")).strip()
    lines = [f"继续并重试上一任务：{original}"]
    if error:
        lines.append(f"上次失败原因：{error}")
    lines.append("请基于当前工程源码重新检查并规划，从失败处修复后完成原目标。")
    return "\n".join(lines)


BootstrapFactory = Callable[..., RuntimeContext]
WorkflowRunner = Callable[..., WorkflowRunResult]
SpecializedBootstrapFactory = Callable[..., SpecializedRuntimeContext]
SpecializedWorkflowRunner = Callable[..., SpecializedWorkflowRunResult]
SpecializedWorkflowResumer = Callable[..., SpecializedWorkflowRunResult]
AgentBootstrapFactory = Callable[..., AgentRuntimeContext]
AgentWorkflowRunner = Callable[..., AgentWorkflowRunResult]
AgentWorkflowResumer = Callable[..., AgentWorkflowRunResult]
ContinuousAgentContextFactory = Callable[..., ContinuousAgentRuntimeContext]
PortDiscoverer = Callable[[], list[SerialPortInfo]]
ProjectDirectoryPicker = Callable[[], Path | None]
ProjectTrash = Callable[[Path, Path], None]
_StreamItem = tuple[str, dict[str, object] | str]

_SERIAL_PATTERN = re.compile(
    r"COM[1-9]\d*" if os.name == "nt" else r"/dev/tty(?:USB|ACM|S)\d+"
)


def _sse_event(
    event: str,
    data: dict[str, object] | str,
    *,
    event_id: int | None = None,
) -> str:
    """把安全 Python 数据编码成一个完整 SSE 帧。"""

    payload = (
        data
        if isinstance(data, str)
        else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    )
    identifier = f"id: {event_id}\n" if event_id is not None else ""
    return f"{identifier}event: {event}\ndata: {payload}\n\n"


def _conversation_stream_payload(
    stream: object,
    *,
    live: bool,
    pending_approval: object | None = None,
    progress: dict[str, object] | None = None,
) -> dict[str, object]:
    updated_at = getattr(stream, "updated_at", None)
    return {
        "thread_id": str(getattr(stream, "thread_id", "")),
        "user_message": str(getattr(stream, "user_message", "")),
        "assistant_content": str(getattr(stream, "assistant_content", "")),
        "status": str(getattr(stream, "status", "running")),
        "last_sequence": int(getattr(stream, "last_sequence", 0)),
        "last_event": getattr(stream, "last_event", None),
        "updated_at": (
            updated_at.isoformat() if isinstance(updated_at, datetime) else None
        ),
        "live": live,
        "pending_approval": pending_approval,
        "progress": progress,
    }


def _text_chunks(text: str, size: int = 10) -> Generator[str, None, None]:
    """Split display prose into small SSE chunks for visible incremental text."""

    for start in range(0, len(text), size):
        yield text[start : start + size]


def _default_ui_path() -> Path:
    return Path(__file__).with_name("ui") / "index.html"


def _select_directory(title: str) -> Path | None:
    window = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            parent=window,
            title=title,
            mustexist=True,
        )
    except Exception as error:
        raise RuntimeError("本机文件夹选择器不可用") from error
    finally:
        if window is not None:
            window.destroy()
    return Path(selected) if selected else None


def _select_project_directory() -> Path | None:
    """用本机文件夹选择器选择项目；取消时返回 None。"""

    return _select_directory("选择 ESP-IDF 项目文件夹")


def _select_espidf_directory() -> Path | None:
    return _select_directory("选择 ESP-IDF 根目录")


def _move_project_to_trash(project_path: Path, projects_root: Path) -> None:
    """Atomically remove a project from its root into a recoverable trash area."""

    resolved_project = project_path.resolve(strict=True)
    resolved_root = projects_root.resolve(strict=True)
    if resolved_project.parent != resolved_root:
        raise OSError("project is outside its configured root")
    if resolved_project.is_symlink() or resolved_project.is_junction():
        raise OSError("linked projects cannot be deleted")

    trash_root = resolved_root / ".luxar-trash"
    if trash_root.exists() and (
        trash_root.is_symlink() or trash_root.is_junction()
    ):
        raise OSError("project trash is unsafe")
    trash_root.mkdir(exist_ok=True)
    destination = trash_root / f"{resolved_project.name}-{uuid.uuid4().hex}"
    resolved_project.replace(destination)


def create_app(
    *,
    projects_roots: Sequence[Path],
    bootstrap_factory: BootstrapFactory = build_deepseek_runtime_context,
    workflow_runner: WorkflowRunner = run_workflow,
    specialized_bootstrap_factory: SpecializedBootstrapFactory = (
        build_deepseek_specialized_runtime_context
    ),
    specialized_workflow_runner: SpecializedWorkflowRunner = (
        run_specialized_workflow
    ),
    specialized_workflow_resumer: SpecializedWorkflowResumer = (
        resume_specialized_workflow
    ),
    agent_bootstrap_factory: AgentBootstrapFactory = (
        build_deepseek_agent_runtime_context
    ),
    agent_workflow_runner: AgentWorkflowRunner = run_agent_workflow,
    agent_workflow_resumer: AgentWorkflowResumer = resume_agent_workflow,
    agent_runtime_mode: AgentRuntimeMode | None = None,
    continuous_agent_enabled: bool | None = None,
    continuous_agent_context_factory: ContinuousAgentContextFactory | None = None,
    ui_path: Path | None = None,
    max_concurrent_workflows: int = 2,
    serial_port: str | None = None,
    target_chip: str | None = None,
    port_discoverer: PortDiscoverer = discover_serial_ports,
    persistence: PersistencePort | None = None,
    storage_runtime: LocalStorageRuntime | None = None,
    knowledge_service: KnowledgeService | None = None,
    sdk_example_knowledge: SdkExampleKnowledgeBase | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    project_creator: EspIdfProjectPort | None = None,
    project_directory_picker: ProjectDirectoryPicker = _select_project_directory,
    toolchain_manager: EspIdfToolchainManager | None = None,
    toolchain_directory_picker: ProjectDirectoryPicker = _select_espidf_directory,
    conversation_router: ConversationRouter | None = None,
    project_trash: ProjectTrash = _move_project_to_trash,
    model_config_store: ModelConfigStore | None = None,
) -> FastAPI:
    """创建可测试的 Web 应用；测试可注入 Fake Bootstrap 与 Runner。

    serial_port 是任务默认值；target_chip 只为未绑定芯片的旧项目兜底。
    项目根列表在启动时配置，也可由本机文件夹选择器显式扩展。
    """

    if max_concurrent_workflows <= 0:
        raise ValueError("max_concurrent_workflows 必须是正整数")

    configured_roots = tuple(projects_roots)
    if not configured_roots:
        raise ValueError("projects_roots 至少需要一个项目根目录")

    catalogs = [WebProjectCatalog(root) for root in configured_roots]
    roots = [catalog.root for catalog in catalogs]
    root_labels = [
        root.name or f"root-{index}"
        for index, root in enumerate(roots)
    ]
    catalog_lock = threading.RLock()
    selected_ui = ui_path or _default_ui_path()
    selected_toolchain_manager = toolchain_manager or EspIdfToolchainManager(
        config_path=roots[0] / ".luxar" / "toolchain.json",
    )
    selected_project_creator = project_creator
    production_bootstrap = bootstrap_factory is build_deepseek_runtime_context
    production_agent_bootstrap = (
        agent_bootstrap_factory is build_deepseek_agent_runtime_context
    )
    production_specialized_bootstrap = (
        specialized_bootstrap_factory
        is build_deepseek_specialized_runtime_context
    )
    injected_legacy_web_seam = (
        not production_bootstrap
        or workflow_runner is not run_workflow
        or not production_specialized_bootstrap
        or specialized_workflow_runner is not run_specialized_workflow
        or specialized_workflow_resumer is not resume_specialized_workflow
        or not production_agent_bootstrap
        or agent_workflow_runner is not run_agent_workflow
        or agent_workflow_resumer is not resume_agent_workflow
        or conversation_router is not None
    )
    # Existing embedding/tests may inject the old pair. Keep that seam only
    # when the dedicated pair itself was left at its defaults.
    legacy_specialized_compat = (
        production_specialized_bootstrap
        and specialized_workflow_runner is run_specialized_workflow
        and (
            not production_bootstrap
            or workflow_runner is not run_workflow
        )
    )
    selected_specialized_bootstrap: Callable[..., object] = (
        bootstrap_factory
        if legacy_specialized_compat
        else specialized_bootstrap_factory
    )
    selected_specialized_runner: Callable[..., object] = (
        workflow_runner
        if legacy_specialized_compat
        else specialized_workflow_runner
    )
    selected_specialized_resumer: Callable[..., object] = (
        resume_workflow
        if legacy_specialized_compat
        and specialized_workflow_resumer is resume_specialized_workflow
        else specialized_workflow_resumer
    )
    selected_runtime = select_firmware_runtime(
        qualification=(
            current_supervisor_qualification()
            if production_bootstrap and production_agent_bootstrap
            else None
        ),
        override=agent_runtime_mode,
    )
    continuous_override = continuous_agent_enabled
    if continuous_override is None and injected_legacy_web_seam:
        # Tests and embedders that replace one side of the legacy/supervisor
        # seam keep their explicitly injected runtime. The production Web app
        # uses the continuous Agent by default.
        continuous_override = False
    selected_continuous_agent = select_continuous_agent(
        override=continuous_override,
    )
    selected_continuous_rollout = select_continuous_agent_rollout(
        selected_continuous_agent
    )

    def _continuous_project_mode(project_key: str) -> str:
        return selected_continuous_rollout.mode_for(project_key)

    def _continuous_project_enabled(project_key: str) -> bool:
        return _continuous_project_mode(project_key) == "enabled"
    selected_model_store = model_config_store or ModelConfigStore(
        roots[0] / ".luxar" / "model-config.json"
    )
    selected_conversation_router = conversation_router
    if selected_conversation_router is None and production_bootstrap:
        # 正式入口的每条消息都交给模型选择处理模式。
        selected_conversation_router = DeepSeekConversationRouter(
            settings_loader=lambda: selected_model_store.load().conversation,
            status_tool=AgentStatusTool(
                config_loader=selected_model_store.load,
                toolchain_status_loader=lambda: selected_toolchain_manager.status,
                workflow_status_loader=lambda: _entry_runtime_status(),
            ),
        )
    capacity = threading.BoundedSemaphore(max_concurrent_workflows)
    active_projects: set[str] = set()
    active_lock = threading.Lock()
    # 这里只保留同进程 SSE 等待器；审批事实由 PersistencePort 保存。
    pending_approvals: dict[str, dict[str, object]] = {}
    pending_lock = threading.Lock()
    continuous_active_sessions: dict[str, dict[str, object]] = {}
    continuous_active_lock = threading.Lock()

    def _entry_runtime_status() -> dict[str, object]:
        with active_lock:
            active_count = len(active_projects)
        with pending_lock:
            pending_count = len(pending_approvals)
        return {
            "status": "busy" if active_count else "idle",
            "active_workflows": active_count,
            "pending_approvals": pending_count,
            "capacity": max_concurrent_workflows,
            "firmware_runtime": selected_runtime.mode,
            "runtime_selection_reason": selected_runtime.reason,
            "legacy_deprecated": selected_runtime.legacy_deprecated,
            "legacy_rollback_available": (
                selected_runtime.legacy_rollback_available
            ),
            "continuous_agent_v2": selected_continuous_agent.enabled,
            "continuous_agent_v2_reason": selected_continuous_agent.reason,
            "continuous_agent_rollout": selected_continuous_rollout.model_dump(
                mode="json"
            ),
        }

    storage: PersistencePort = persistence or TransientPersistence()

    def _continuous_agent_context(
        *,
        project_path: Path,
        target_chip: str | None,
        selected_serial_port: str | None,
        allow_dependency_downloads: bool,
        persistence: PersistencePort,
        drain_steering: Callable[[], list[SteeringMessage]] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> ContinuousAgentRuntimeContext:
        if continuous_agent_context_factory is not None:
            return continuous_agent_context_factory(
                project_path=project_path,
                target_chip=target_chip,
                serial_port=selected_serial_port,
                allow_dependency_downloads=allow_dependency_downloads,
                persistence=persistence,
                drain_steering=drain_steering,
                cancellation_requested=cancellation_requested,
            )
        endpoint = selected_model_store.load().conversation.resolved()
        if not endpoint.configured:
            raise CapabilityError(
                category="authentication",
                message="持续 Agent 对话模型尚未配置",
                retryable=False,
            )
        workspace = LocalWorkspaceAdapter()
        builder = None
        device = None
        if selected_toolchain_manager.status.available:
            idf_command = _require_idf_command()
            builder = EspIdfCliAdapter(
                idf_command=idf_command,
                allow_dependency_downloads=allow_dependency_downloads,
            )
            device = EspIdfDeviceAdapter(idf_command=idf_command)
        registry = create_core_tool_registry(
            workspace=workspace,
            code_executor=LocalChangeBundleExecutor(workspace),
            knowledge=app.state.knowledge_service,
            builder=builder,
            flasher=device,
            monitor=device,
            persistence=persistence,
            ledger=persistence,
            target_chip=target_chip,
        )
        domain_workflows = DomainWorkflowRegistry()
        selected_checkpointer: BaseCheckpointSaver | None = (
            app.state.checkpointer
        )
        if selected_checkpointer is not None:
            supervisor_context = build_deepseek_agent_runtime_context(
                project_path=project_path,
                build_executor=builder,
                workspace=workspace,
                flasher=device,
                monitor=device,
                serial_port=selected_serial_port,
                settings=endpoint,
                client=DeepSeekJsonClient(endpoint),
                allow_dependency_downloads=allow_dependency_downloads,
                idf_command=(
                    selected_toolchain_manager.command or ("idf.py",)
                ),
            )
            domain_workflows.register(
                ProjectChangeWorkflow(
                    runtime_context=supervisor_context,
                    checkpointer=selected_checkpointer,
                    persistence=persistence,
                )
            )
        continuous_stepper = DeepSeekContinuousAgentStep(
            OpenAICompatibleJsonClient(endpoint),
            endpoint.model,
        )
        return ContinuousAgentRuntimeContext(
            stepper=continuous_stepper,
            reply_streamer=continuous_stepper,
            tools=registry,
            project_path=project_path,
            domain_workflows=domain_workflows,
            context_compactor=continuous_stepper,
            drain_steering=drain_steering,
            cancellation_requested=cancellation_requested,
        )

    def _observe_continuous_agent_shadow(
        *,
        project_path: Path,
        project_key: str,
        user_message: str,
        client_turn_id: str | None,
        legacy_intent: str,
        selected_serial_port: str | None,
        selected_target_chip: str | None,
        allow_dependency_downloads: bool,
        persistence: PersistencePort,
    ) -> None:
        if _continuous_project_mode(project_key) != "shadow":
            return
        shadow_turn_id = f"shadow_{uuid.uuid4().hex}"
        interaction_id = (
            f"continuous-shadow:{project_key}:"
            f"{client_turn_id or shadow_turn_id}"
        )
        try:
            shadow_context = _continuous_agent_context(
                project_path=project_path,
                target_chip=selected_target_chip,
                selected_serial_port=selected_serial_port,
                allow_dependency_downloads=allow_dependency_downloads,
                persistence=persistence,
            )
            messages = persistence.get_messages(project_key)[-20:]
            events = [
                ConversationEvent(
                    event_id=f"{shadow_turn_id}:history:{index}",
                    turn_id=shadow_turn_id,
                    kind=(
                        "assistant_message"
                        if item.get("role") == "assistant"
                        else "user_message"
                    ),
                    sequence=index,
                    payload={"content": str(item.get("content", ""))},
                )
                for index, item in enumerate(messages, start=1)
                if str(item.get("content", "")).strip()
            ]
            events.append(
                ConversationEvent(
                    event_id=f"{shadow_turn_id}:user",
                    turn_id=shadow_turn_id,
                    kind="user_message",
                    sequence=len(events) + 1,
                    payload={"content": user_message},
                )
            )
            step = shadow_context.stepper.decide_next_step(
                AgentStepContext(
                    session_id=shadow_turn_id,
                    turn_id=shadow_turn_id,
                    project_key=project_key,
                    recent_events=events,
                    resolved_inputs={
                        key: value
                        for key, value in {
                            "serial_port": selected_serial_port,
                            "target_chip": selected_target_chip,
                        }.items()
                        if value is not None
                    },
                    tools=shadow_context.tools.descriptors(),
                    domain_workflows=[
                        item.model_dump(mode="json")
                        for item in shadow_context.domain_workflows.descriptors()
                    ],
                )
            )
            payload: dict[str, object] = {
                "status": "completed",
                **compare_shadow_decision(
                    legacy_intent,
                    step,
                ).model_dump(mode="json"),
            }
        except Exception as error:
            # Shadow is observational. It must never change the legacy result
            # or expose provider error text into the durable audit record.
            payload = {
                "status": "failed",
                "legacy_intent": legacy_intent,
                "error_type": type(error).__name__,
            }
        persistence.append_agent_interaction(
            interaction_id=interaction_id,
            project_key=project_key,
            objective_id=None,
            kind="continuous_agent_shadow_decision",
            payload=payload,
        )

    def _active_continuous_session(
        *,
        project_key: str,
        requested_session_id: str | None,
    ) -> tuple[str, dict[str, object]]:
        if not _continuous_project_enabled(project_key):
            raise HTTPException(
                status_code=409,
                detail="持续 Agent V2 尚未启用",
            )
        with continuous_active_lock:
            if requested_session_id is not None:
                entry = continuous_active_sessions.get(requested_session_id)
                candidates = (
                    [(requested_session_id, entry)] if entry is not None else []
                )
            else:
                candidates = [
                    (session_id, entry)
                    for session_id, entry in continuous_active_sessions.items()
                    if entry.get("project_key") == project_key
                ]
            if not candidates:
                raise HTTPException(
                    status_code=409,
                    detail="当前项目没有正在运行的持续 Agent Turn",
                )
            session_id, entry = candidates[0]
            if entry.get("project_key") != project_key:
                raise HTTPException(
                    status_code=409,
                    detail="Agent Session 不属于当前项目",
                )
            return session_id, dict(entry)
    local_runtime = storage_runtime
    if local_runtime is None and persistence is None:
        if (
            production_bootstrap
            or production_agent_bootstrap
            or "LUXAR_STORAGE_DIRECTORY" in os.environ
        ):
            local_settings = LocalStorageSettings.for_projects_root(roots[0])
        else:
            local_settings = LocalStorageSettings(
                directory=roots[0] / ".luxar" / "storage"
            )
        local_runtime = LocalStorageRuntime(local_settings)

    manage_knowledge_service = knowledge_service is None

    def _build_configured_knowledge_service(
        config: RuntimeModelConfig,
    ) -> KnowledgeService | None:
        if local_runtime is None or not config.embedding.configured:
            return None
        if config.embedding.mode == "local_hash":
            embeddings = LocalHashEmbeddingAdapter(config.embedding.dimensions)
        else:
            settings = KnowledgeSettings(
                api_key=config.embedding.sdk_api_key(),
                base_url=config.embedding.base_url,
                model=config.embedding.model,
                dimensions=config.embedding.dimensions,
                timeout_seconds=config.embedding.timeout_seconds,
            )
            embeddings = OpenAIEmbeddingAdapter(settings)
        atom_extractor = None
        if config.conversation.configured:
            endpoint = config.conversation.resolved()
            atom_extractor = DeepSeekKnowledgeAtomExtractor(
                DeepSeekJsonClient(endpoint),
                endpoint.model,
            )
        return KnowledgeService(
            LanceDBKnowledgeIndex(
                local_runtime.settings.knowledge_path,
                dimensions=embeddings.dimensions,
            ),
            embeddings,
            atom_extractor=atom_extractor,
        )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        nonlocal storage, knowledge_service, sdk_example_knowledge
        if local_runtime is not None:
            local_runtime.open()
            storage = local_runtime.persistence
            application.state.persistence = storage
            application.state.checkpointer = local_runtime.checkpointer()
            if manage_knowledge_service:
                knowledge_service = _build_configured_knowledge_service(
                    selected_model_store.load()
                )
                application.state.knowledge_service = knowledge_service
            if sdk_example_knowledge is None:
                sdk_embeddings = LocalHashEmbeddingAdapter()
                sdk_example_knowledge = SdkExampleKnowledgeBase(
                    LanceDBKnowledgeIndex(
                        local_runtime.settings.sdk_knowledge_path,
                        dimensions=sdk_embeddings.dimensions,
                    ),
                    sdk_embeddings,
                )
                application.state.sdk_example_knowledge = sdk_example_knowledge
        try:
            yield
        finally:
            wait_for_continuous_agent_workers()
            if local_runtime is not None:
                local_runtime.close()

    app = FastAPI(
        title="LUXAR LangGraph API",
        version="0.2.0",
        lifespan=lifespan,
    )
    # 测试通过 app.state 观察待处理审批。
    app.state.pending_approvals = pending_approvals  # type: ignore[attr-defined]
    app.state.persistence = storage
    app.state.checkpointer = (
        checkpointer
        if checkpointer is not None
        else InMemorySaver(serde=create_checkpoint_serializer())
        if local_runtime is None and selected_continuous_agent.enabled
        else None
    )
    app.state.knowledge_service = knowledge_service
    app.state.sdk_example_knowledge = sdk_example_knowledge
    app.state.storage_runtime = local_runtime
    app.state.toolchain_manager = selected_toolchain_manager
    app.state.model_config_store = selected_model_store
    app.state.continuous_agent_selection = selected_continuous_agent
    app.state.continuous_agent_rollout = selected_continuous_rollout
    app.state.continuous_active_sessions = continuous_active_sessions

    def _toolchain_contract(
        status: EspIdfToolchainStatus,
    ) -> WebEspIdfToolchain:
        return WebEspIdfToolchain(
            available=status.available,
            source=status.source,
            version=status.version,
            idf_path=status.idf_path,
            message=status.message,
        )

    def _require_idf_command() -> tuple[str, ...]:
        command = selected_toolchain_manager.command
        if command is None:
            raise HTTPException(
                status_code=503,
                detail="未检测到可用的 ESP-IDF 环境，请先在仪表盘配置工具链",
            )
        return command

    def _configured_pdf_reader() -> PdfDocumentReader:
        endpoint = selected_model_store.load().vision_endpoint()
        return PdfDocumentReader(
            drawing_analyzer=configured_drawing_analyzer(
                endpoint,
                use_environment=False,
            ),
            visual_max_workers=(
                1 if endpoint is None or endpoint.uses_local_execution else 4
            ),
        )

    def _merged_endpoint(
        body: WebModelEndpointUpdate,
        previous: ModelEndpoint | None,
    ) -> ModelEndpoint:
        previous_key = (
            previous.api_key
            if previous is not None and previous.provider == body.provider
            else None
        )
        api_key = (
            None
            if body.clear_api_key
            else body.api_key.strip()
            if body.api_key is not None and body.api_key.strip()
            else previous_key
        )
        return ModelEndpoint(
            provider=body.provider,
            api_key=api_key,
            base_url=body.base_url.strip(),
            model=body.model.strip(),
            repair_model=body.model.strip(),
            timeout_seconds=body.timeout_seconds,
            context_window_tokens=(
                body.context_window_tokens
                if body.context_window_tokens is not None
                else previous.context_window_tokens
                if previous is not None
                and previous.provider == body.provider
                and previous.model.strip() == body.model.strip()
                else None
            ),
        ).resolved()

    def _merged_embedding(
        body: WebEmbeddingUpdate,
        previous: EmbeddingConfig,
    ) -> EmbeddingConfig:
        previous_key = (
            previous.api_key
            if previous.provider == body.provider
            else None
        )
        api_key = (
            None
            if body.clear_api_key
            else body.api_key.strip()
            if body.api_key is not None and body.api_key.strip()
            else previous_key
        )
        return EmbeddingConfig(
            mode=body.mode,
            provider=body.provider,
            api_key=api_key,
            base_url=body.base_url.strip(),
            model=body.model.strip(),
            dimensions=body.dimensions,
            timeout_seconds=body.timeout_seconds,
        )

    def _root_index(raw: object) -> int:
        with catalog_lock:
            if not isinstance(raw, int) or raw < 0 or raw >= len(roots):
                raise HTTPException(status_code=422, detail="项目根索引无效")
            return raw

    def resolve_project(project: str, root_index: int) -> Path:
        catalog = catalogs[_root_index(root_index)]
        try:
            return catalog.resolve(project)
        except WebProjectError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    def _task_key(root_index: int, project: str) -> str:
        return f"{root_index}:{project}"

    def _agent_snapshot(
        project: str,
        root_index: int,
        *,
        interaction_limit: int = 100,
    ) -> WebAgentSnapshot:
        resolve_project(project, root_index)
        if not 1 <= interaction_limit <= 500:
            raise HTTPException(status_code=422, detail="limit 必须在 1 到 500 之间")
        current: PersistencePort = app.state.persistence
        project_key = _task_key(root_index, project)
        try:
            pending = current.get_pending_approval(project_key)
            if (
                pending is not None
                and pending.runtime_config.get("workflow_family")
                == "knowledge_task"
            ):
                pending_state: SpecializedWorkflowState = {
                    "task_text": str(
                        pending.runtime_config.get("task_text", "知识任务")
                    ),
                    "task_mode": "knowledge",
                    "status": "running",
                    "trace": ["analyze_knowledge_task"],
                    "interaction": pending.request,
                }
                operation = pending.request.get("operation")
                if isinstance(operation, dict):
                    pending_state["knowledge_task"] = operation
                return workbench_snapshot_contract(
                    project=project,
                    root_index=root_index,
                    record=WorkbenchSnapshotRecord(
                        project_key=project_key,
                        workflow_family="knowledge_task",
                        thread_id=pending.thread_id,
                        snapshot=knowledge_workbench_snapshot(
                            pending_state,
                            thread_id=pending.thread_id,
                            awaiting_user=True,
                        ),
                        updated_at=datetime.now(timezone.utc),
                    ),
                )

            workbench = current.get_workbench_snapshot(project_key)
            if (
                workbench is not None
                and workbench.workflow_family == "knowledge_task"
            ):
                return workbench_snapshot_contract(
                    project=project,
                    root_index=root_index,
                    record=workbench,
                )

            record = current.get_agent_project(project_key)
            if workbench is None:
                latest = current.get_latest_run(project_key)
                if (
                    latest is not None
                    and (
                        latest.workflow_family == "knowledge_task"
                        or "knowledge_task" in latest.result
                    )
                ):
                    result_state: SpecializedWorkflowState = {
                        "task_text": latest.task_text,
                        "task_mode": "knowledge",
                        "status": (
                            "running"
                            if latest.status in {"running", "pending_approval"}
                            else str(
                                latest.result.get("status", latest.status)
                            )
                        ),
                        "trace": list(latest.result.get("trace", [])),
                    }
                    if "knowledge_task" in latest.result:
                        result_state["knowledge_task"] = latest.result[
                            "knowledge_task"
                        ]
                    if "knowledge_result" in latest.result:
                        result_state["knowledge_result"] = latest.result[
                            "knowledge_result"
                        ]
                    if latest.result.get("error") is not None:
                        result_state["error"] = latest.result["error"]
                    return workbench_snapshot_contract(
                        project=project,
                        root_index=root_index,
                        record=WorkbenchSnapshotRecord(
                            project_key=project_key,
                            workflow_family="knowledge_task",
                            thread_id=latest.thread_id,
                            snapshot=knowledge_workbench_snapshot(
                                result_state,
                                thread_id=latest.thread_id,
                                awaiting_user=(
                                    latest.status == "pending_approval"
                                ),
                            ),
                            updated_at=datetime.now(timezone.utc),
                        ),
                    )
            if record is None:
                raise HTTPException(status_code=404, detail="项目尚无 Agent 状态")
            snapshot = agent_snapshot_contract(
                project=project,
                root_index=root_index,
                record=record,
                interactions=current.get_agent_interactions(
                    project_key,
                    limit=interaction_limit,
                ),
            )
            if workbench is not None:
                snapshot = snapshot.model_copy(
                    update={"thread_id": workbench.thread_id}
                )
            return snapshot
        except ValidationError as error:
            logger.error("Invalid persisted agent snapshot: %s", error)
            raise HTTPException(status_code=500, detail="Agent 状态快照无效") from error

    def _validate_task_port(serial_port: str | None) -> None:
        if serial_port is None:
            return

        if not _SERIAL_PATTERN.fullmatch(serial_port):
            raise HTTPException(status_code=422, detail="串口名称无效")

        try:
            discovered = {
                port.name for port in port_discoverer()
            }
        except Exception:
            # 发现失败时拒绝带串口的请求，绝不把未验证的串口交给 idf.py。
            raise HTTPException(
                status_code=503,
                detail="串口设备发现失败，请稍后重试",
            )

        if serial_port not in discovered:
            raise HTTPException(
                status_code=422,
                detail="串口不在当前已发现的设备列表中",
            )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        if not selected_ui.is_file():
            raise HTTPException(status_code=503, detail="Web UI 尚未安装")
        return FileResponse(
            selected_ui,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/health", response_model=WebHealth)
    def health() -> WebHealth:
        return WebHealth()

    @app.get("/api/runtime")
    def runtime_status() -> dict[str, object]:
        retirement = current_legacy_retirement()
        return {
            **selected_runtime.model_dump(mode="json"),
            "continuous_agent_v2": selected_continuous_agent.model_dump(
                mode="json"
            ),
            "continuous_agent_rollout": selected_continuous_rollout.model_dump(
                mode="json"
            ),
            "legacy_retirement_ready": retirement.ready_for_removal,
            "legacy_retirement_blocking_gates": (
                retirement.blocking_gate_ids
            ),
        }

    @app.get("/api/runtime/audit")
    def runtime_audit() -> dict[str, object]:
        checkpoint_threads: set[str] | None = None
        if local_runtime is not None:
            try:
                checkpoint_threads = inspect_sqlite_checkpoint_threads(
                    local_runtime.settings.checkpoint_path
                )
            except (OSError, sqlite3.Error):
                checkpoint_threads = None
        report = audit_runtime_retirement(
            app.state.persistence,
            checkpoint_thread_ids=checkpoint_threads,
        )
        return report.model_dump(mode="json")

    @app.get("/api/health/database")
    @app.get("/api/health/storage")
    def storage_health() -> dict[str, object]:
        current: PersistencePort = app.state.persistence
        healthy = current.health()
        if not healthy:
            raise HTTPException(status_code=503, detail="数据库健康检查失败")
        return {
            "status": "ok",
            "database": "sqlite" if current.durable else "disabled",
            "durable": current.durable,
            **(
                {
                    "application_path": str(
                        local_runtime.settings.application_path
                    ),
                    "checkpoint_path": str(
                        local_runtime.settings.checkpoint_path
                    ),
                    "knowledge_path": str(
                        local_runtime.settings.knowledge_path
                    ),
                    "sdk_knowledge_path": str(
                        local_runtime.settings.sdk_knowledge_path
                    ),
                }
                if local_runtime is not None
                else {}
            ),
        }

    @app.get("/api/config/models")
    def get_model_config() -> dict[str, object]:
        try:
            return selected_model_store.load().public_dict()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=500, detail="模型配置读取失败") from error

    @app.put("/api/config/models")
    def put_model_config(body: WebModelConfigUpdate) -> dict[str, object]:
        try:
            previous = selected_model_store.load()
            conversation = _merged_endpoint(body.conversation, previous.conversation)
            if not conversation.configured:
                raise HTTPException(status_code=422, detail="对话模型配置不完整")
            vision = previous.vision
            if body.vision is not None:
                vision = _merged_endpoint(body.vision, previous.vision)
            if body.vision_mode == "separate" and (
                vision is None or not vision.configured
            ):
                raise HTTPException(status_code=422, detail="多模态模型配置不完整")
            embedding = previous.embedding
            if body.embedding is not None:
                embedding = _merged_embedding(body.embedding, previous.embedding)
            if not embedding.configured:
                raise HTTPException(status_code=422, detail="Embedding 配置不完整")
            config = RuntimeModelConfig(
                conversation=conversation,
                vision_mode=body.vision_mode,
                vision=vision,
                embedding=embedding,
            )
            proposed_knowledge_service = None
            if manage_knowledge_service and local_runtime is not None:
                try:
                    proposed_knowledge_service = _build_configured_knowledge_service(
                        config
                    )
                except ValueError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
            selected_model_store.save(config)
            if manage_knowledge_service and local_runtime is not None:
                app.state.knowledge_service = proposed_knowledge_service
            return config.public_dict()
        except HTTPException:
            raise
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=500, detail="模型配置保存失败") from error

    @app.get(
        "/api/toolchains/espidf",
        response_model=WebEspIdfToolchain,
    )
    def get_espidf_toolchain() -> WebEspIdfToolchain:
        return _toolchain_contract(selected_toolchain_manager.status)

    @app.post(
        "/api/toolchains/espidf/refresh",
        response_model=WebEspIdfToolchain,
    )
    def refresh_espidf_toolchain() -> WebEspIdfToolchain:
        return _toolchain_contract(selected_toolchain_manager.refresh())

    @app.post(
        "/api/toolchains/espidf/select-directory",
        response_model=WebEspIdfToolchain,
    )
    def select_espidf_toolchain() -> WebEspIdfToolchain:
        try:
            selected = toolchain_directory_picker()
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail="无法打开本机文件夹选择器",
            ) from error
        if selected is None:
            return _toolchain_contract(selected_toolchain_manager.status)
        try:
            status = selected_toolchain_manager.configure(Path(selected))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except OSError as error:
            raise HTTPException(
                status_code=503,
                detail="ESP-IDF 工具链配置保存失败",
            ) from error
        return _toolchain_contract(status)

    @app.get(
        "/api/workspace/projects",
        response_model=WebProjectList,
        response_model_exclude_none=True,
    )
    def list_projects() -> WebProjectList:
        with catalog_lock:
            root_items = [
                WebProjectRoot(index=index, label=label)
                for index, label in enumerate(root_labels)
            ]
            projects: list[WebProject] = []
            for index, catalog in enumerate(catalogs):
                for project in catalog.list_projects():
                    projects.append(
                        WebProject(
                            name=project.name,
                            platform=project.platform,
                            root_index=index,
                            target_chip=project.target_chip,
                        )
                    )
        projects.sort(
            key=lambda item: (item.root_index, item.name.casefold())
        )
        return WebProjectList(roots=root_items, projects=projects)

    @app.post(
        "/api/workspace/projects/select-directory",
        response_model=WebProjectSelection,
    )
    def select_project_directory(
        body: WebProjectSelectionRequest,
    ) -> WebProjectSelection:
        try:
            selected = project_directory_picker()
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail="无法打开本机文件夹选择器",
            ) from error
        if selected is None:
            return WebProjectSelection()

        try:
            selected_path = Path(selected).resolve(strict=True)
            selected_parent = selected_path.parent
            with catalog_lock:
                root_index = next(
                    (
                        index
                        for index, root in enumerate(roots)
                        if root == selected_parent
                    ),
                    -1,
                )
                if root_index < 0:
                    catalog = WebProjectCatalog(selected_parent)
                    catalog.resolve(selected_path.name)
                    creator = selected_project_creator or EspIdfProjectAdapter()
                    creator.create_project(
                        parent_dir=selected_parent,
                        project_name=selected_path.name,
                        target_chip=body.target_chip,
                    )
                    catalogs.append(catalog)
                    roots.append(catalog.root)
                    root_labels.append(catalog.root.name or f"root-{len(roots) - 1}")
                    root_index = len(roots) - 1
                else:
                    catalogs[root_index].resolve(selected_path.name)
                    creator = selected_project_creator or EspIdfProjectAdapter()
                    creator.create_project(
                        parent_dir=selected_parent,
                        project_name=selected_path.name,
                        target_chip=body.target_chip,
                    )
        except EspIdfError as error:
            if error.category != "invalid_project":
                raise HTTPException(
                    status_code=503,
                    detail="项目芯片配置保存失败",
                ) from error
            raise HTTPException(
                status_code=409,
                detail="项目芯片已固定，不能更改",
            ) from error
        except (OSError, RuntimeError, WebProjectError) as error:
            raise HTTPException(
                status_code=422,
                detail="所选文件夹不是有效的 ESP-IDF 项目",
            ) from error

        return WebProjectSelection(
            project=WebProject(
                name=selected_path.name,
                platform="espidf",
                root_index=root_index,
                target_chip=body.target_chip,
            )
        )

    @app.post(
        "/api/workspace/projects",
        response_model=WebProject,
        response_model_exclude_none=True,
    )
    def create_project(body: WebProjectCreateRequest) -> WebProject:
        root_index = _root_index(body.root_index)
        root = roots[root_index]
        candidate = root / body.name
        if candidate.exists():
            raise HTTPException(status_code=409, detail="项目名称已存在")
        creator = selected_project_creator
        if creator is None:
            creator = EspIdfProjectAdapter(idf_command=_require_idf_command())
        try:
            creator.create_project(
                parent_dir=root,
                project_name=body.name,
                target_chip=body.target_chip,
            )
            # 重新通过 WebProjectCatalog 的路径与 ESP-IDF 结构校验。
            catalogs[root_index].resolve(body.name)
        except EspIdfError as error:
            status = 422 if error.category == "invalid_project" else 503
            message = (
                "项目名称或目标芯片无效"
                if status == 422
                else "ESP-IDF 项目创建环境不可用"
            )
            raise HTTPException(status_code=status, detail=message) from error
        except WebProjectError as error:
            raise HTTPException(
                status_code=500,
                detail="新项目结构校验失败",
            ) from error
        return WebProject(
            name=body.name,
            platform="espidf",
            root_index=root_index,
            target_chip=body.target_chip,
        )

    @app.delete("/api/projects/{project}")
    def delete_project(
        project: str,
        root_index: int = 0,
    ) -> dict[str, object]:
        project_path = resolve_project(project, root_index)
        task_key = _task_key(root_index, project)
        current: PersistencePort = app.state.persistence
        if current.get_pending_approval(task_key) is not None:
            raise HTTPException(
                status_code=409,
                detail="项目有待处理的烧录审批，暂时不能删除",
            )

        with active_lock:
            if task_key in active_projects:
                raise HTTPException(
                    status_code=409,
                    detail="项目有正在运行的任务，暂时不能删除",
                )
            # 删除期间占用项目槽，避免新任务在路径移动前并发启动。
            active_projects.add(task_key)

        try:
            project_trash(project_path, roots[root_index])
            current.reset_conversation(task_key)
        except OSError as error:
            raise HTTPException(
                status_code=500,
                detail="项目文件夹删除失败",
            ) from error
        finally:
            with active_lock:
                active_projects.discard(task_key)

        return {
            "status": "deleted",
            "project": project,
            "recoverable": True,
        }

    @app.get("/api/devices/ports", response_model=WebSerialPortList)
    def list_ports() -> WebSerialPortList:
        try:
            ports = port_discoverer()
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="串口设备发现失败",
            )

        return WebSerialPortList(
            ports=[
                WebSerialPort(
                    name=port.name,
                    description=port.description,
                    hardware_id=port.hardware_id,
                )
                for port in ports
            ]
        )

    @app.get(
        "/api/projects/{project}/agent",
        response_model=WebAgentSnapshot,
    )
    def get_agent_snapshot(
        project: str,
        root_index: int = 0,
    ) -> WebAgentSnapshot:
        return _agent_snapshot(project, root_index)

    @app.get(
        "/api/projects/{project}/agent/objective",
        response_model=WebAgentObjective,
    )
    def get_agent_objective(
        project: str,
        root_index: int = 0,
    ) -> WebAgentObjective:
        return _agent_snapshot(project, root_index).objective

    @app.get(
        "/api/projects/{project}/agent/tasks",
        response_model=list[WebAgentTask],
    )
    def get_agent_tasks(
        project: str,
        root_index: int = 0,
    ) -> list[WebAgentTask]:
        return _agent_snapshot(project, root_index).tasks

    @app.get(
        "/api/projects/{project}/agent/capabilities",
        response_model=list[WebAgentCapability],
    )
    def get_agent_capabilities(
        project: str,
        root_index: int = 0,
    ) -> list[WebAgentCapability]:
        return _agent_snapshot(project, root_index).capabilities

    @app.get(
        "/api/projects/{project}/agent/evidence",
        response_model=list[WebAgentEvidence],
    )
    def get_agent_evidence(
        project: str,
        root_index: int = 0,
    ) -> list[WebAgentEvidence]:
        return _agent_snapshot(project, root_index).evidence

    @app.get(
        "/api/projects/{project}/agent/interactions",
        response_model=list[WebAgentInteraction],
    )
    def get_agent_interactions(
        project: str,
        root_index: int = 0,
        limit: int = 100,
    ) -> list[WebAgentInteraction]:
        return _agent_snapshot(
            project,
            root_index,
            interaction_limit=limit,
        ).interactions

    @app.post(
        "/api/projects/{project}/agent/interactions",
        response_model=WebAgentInteraction,
        status_code=202,
    )
    def post_agent_interaction(
        project: str,
        body: WebAgentInteractionRequest,
    ) -> WebAgentInteraction:
        resolve_project(project, body.root_index)
        if not _agent_snapshot(project, body.root_index).supports_interactions:
            raise HTTPException(
                status_code=409,
                detail="知识任务请在原审批卡或对话中继续处理",
            )
        current: PersistencePort = app.state.persistence
        project_key = _task_key(body.root_index, project)
        record = current.get_agent_project(project_key)
        if record is None:
            raise HTTPException(status_code=404, detail="项目尚无 Agent 状态")
        objective_id = str(record.objective.get("objective_id", "")) or None
        interaction_id = f"agent:{uuid.uuid4().hex}"
        payload: dict[str, object] = {"message": body.message}
        if body.target_id is not None:
            payload["target_id"] = body.target_id
        current.append_agent_interaction(
            interaction_id=interaction_id,
            project_key=project_key,
            objective_id=objective_id,
            kind=body.kind,
            payload=payload,
        )
        return WebAgentInteraction(
            interaction_id=interaction_id,
            objective_id=objective_id,
            kind=body.kind,
            payload=payload,
            queued=True,
        )

    @app.get("/api/conversations/{project}")
    def get_conversation(
        project: str,
        root_index: int = 0,
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        key = _task_key(root_index, project)
        current: PersistencePort = app.state.persistence
        messages = current.get_messages(key)
        active_session = (
            current.get_active_agent_session(key)
            if _continuous_project_enabled(key)
            else None
        )
        active_stream = current.get_active_conversation_stream(key)
        active_run: dict[str, object] | None = None
        if active_stream is not None:
            with active_lock:
                live = key in active_projects
            approval = current.get_pending_approval(key)
            approval_stream_id = (
                str(approval.runtime_config.get("turn_id", ""))
                if approval is not None
                else ""
            )
            pending_request = (
                approval.request
                if approval is not None
                and (approval_stream_id or approval.thread_id)
                == active_stream.thread_id
                else None
            )
            recent_events = current.list_conversation_stream_events(
                active_stream.thread_id,
                after_sequence=max(0, active_stream.last_sequence - 2000),
                limit=2000,
            )
            latest_progress = next(
                (
                    item.data
                    for item in reversed(recent_events)
                    if item.event == "progress"
                    and isinstance(item.data, dict)
                    and item.data.get("progress_type") == "pdf"
                ),
                None,
            )
            active_run = _conversation_stream_payload(
                active_stream,
                live=live,
                pending_approval=pending_request,
                progress=latest_progress,
            )
        return {
            "messages": messages,
            "project": project,
            "durable": current.durable,
            "active_run": active_run,
            "continuous_agent_v2": _continuous_project_enabled(key),
            "continuous_agent_mode": _continuous_project_mode(key),
            "session_id": (
                active_session.session_id
                if active_session is not None
                else None
            ),
        }

    @app.get("/api/conversations/{project}/streams/{thread_id}")
    def replay_conversation_stream(
        project: str,
        thread_id: str,
        root_index: int = 0,
        after_sequence: int = 0,
    ) -> StreamingResponse:
        resolve_project(project, root_index)
        if after_sequence < 0:
            raise HTTPException(status_code=422, detail="事件序号无效")
        key = _task_key(root_index, project)
        current: PersistencePort = app.state.persistence
        stream = current.get_conversation_stream(thread_id)
        if stream is None or stream.task_key != key:
            raise HTTPException(status_code=404, detail="任务输出流不存在")

        def replay() -> Generator[str, None, None]:
            cursor = after_sequence
            while True:
                records = current.list_conversation_stream_events(
                    thread_id,
                    after_sequence=cursor,
                    limit=500,
                )
                for record in records:
                    cursor = record.sequence
                    yield _sse_event(
                        record.event,
                        record.data,
                        event_id=record.sequence,
                    )
                    if record.event == "done":
                        return
                latest = current.get_conversation_stream(thread_id)
                if latest is None:
                    yield _sse_event(
                        "error",
                        {"category": "recovery", "message": "任务输出已被清理"},
                    )
                    yield _sse_event("done", "[DONE]")
                    return
                if latest.status not in {"running", "pending_approval"}:
                    yield _sse_event("done", "[DONE]")
                    return
                time.sleep(0.2)

        return StreamingResponse(
            replay(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-LUXAR-Thread-ID": thread_id,
            },
        )

    @app.post("/api/conversations/{project}/reset")
    def reset_conversation(
        project: str,
        root_index: int = 0,
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        key = _task_key(root_index, project)
        current: PersistencePort = app.state.persistence
        with active_lock:
            is_active = key in active_projects
        if is_active or current.get_active_conversation_stream(key) is not None:
            raise HTTPException(
                status_code=409,
                detail="任务仍在运行，不能清空当前对话",
            )
        archived_session_id: str | None = None
        new_session_id: str | None = None
        if _continuous_project_enabled(key):
            active_session = current.get_active_agent_session(key)
            if active_session is not None:
                current.archive_agent_session(active_session.session_id)
                archived_session_id = active_session.session_id
            new_session = current.create_agent_session(
                session_id=f"session_{uuid.uuid4().hex}",
                project_key=key,
            )
            new_session_id = new_session.session_id
        # The compatibility transcript is cleared for the old UI. Durable V2
        # Agent Turn records remain available as the audit trail.
        current.reset_conversation(key)
        current.upsert_memory(
            project_key=key,
            memory_key=_CONVERSATION_SUMMARY_KEY,
            memory_type="conversation_context",
            value={"summary": "", "covered_message_count": 0},
        )
        return {
            "status": "ok",
            "project": project,
            "durable": current.durable,
            **(
                {
                    "archived_session_id": archived_session_id,
                    "session_id": new_session_id,
                }
                if _continuous_project_enabled(key)
                else {}
            ),
        }

    @app.get("/api/conversations/{project}/session")
    def get_continuous_agent_session(
        project: str,
        root_index: int = 0,
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        key = _task_key(root_index, project)
        if not _continuous_project_enabled(key):
            return {
                "enabled": False,
                "mode": _continuous_project_mode(key),
                "session": None,
            }
        current: PersistencePort = app.state.persistence
        session = current.get_active_agent_session(key)
        return {
            "enabled": True,
            "mode": "enabled",
            "session": (
                {
                    "session_id": session.session_id,
                    "project_key": session.project_key,
                    "status": session.status,
                    "active_objective_id": session.active_objective_id,
                    "compaction_cursor": session.compaction_cursor,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                }
                if session is not None
                else None
            ),
        }

    @app.get("/api/conversations/{project}/shadow")
    def get_continuous_agent_shadow_summary(
        project: str,
        root_index: int = 0,
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        key = _task_key(root_index, project)
        current: PersistencePort = app.state.persistence
        payloads = [
            dict(item.payload)
            for item in current.get_agent_interactions(key, limit=500)
            if item.kind == "continuous_agent_shadow_decision"
        ]
        return {
            "project": project,
            "mode": _continuous_project_mode(key),
            "summary": summarize_shadow_decisions(payloads).model_dump(
                mode="json"
            ),
        }

    @app.post("/api/conversations/{project}/sessions")
    def create_continuous_agent_session(
        project: str,
        root_index: int = 0,
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        key = _task_key(root_index, project)
        if not _continuous_project_enabled(key):
            raise HTTPException(
                status_code=409,
                detail="持续 Agent V2 尚未启用",
            )
        current: PersistencePort = app.state.persistence
        with active_lock:
            is_active = key in active_projects
        if is_active or current.get_active_conversation_stream(key) is not None:
            raise HTTPException(
                status_code=409,
                detail="任务仍在运行，不能切换 Agent Session",
            )
        previous = current.get_active_agent_session(key)
        if previous is not None:
            current.archive_agent_session(previous.session_id)
        session = current.create_agent_session(
            session_id=f"session_{uuid.uuid4().hex}",
            project_key=key,
        )
        return {
            "status": "ok",
            "project": project,
            "session_id": session.session_id,
            "archived_session_id": (
                previous.session_id if previous is not None else None
            ),
        }

    @app.post("/api/conversations/{project}/steer", status_code=202)
    def steer_continuous_agent(
        project: str,
        body: WebSteeringRequest,
    ) -> dict[str, object]:
        resolve_project(project, body.root_index)
        key = _task_key(body.root_index, project)
        session_id, entry = _active_continuous_session(
            project_key=key,
            requested_session_id=body.session_id,
        )
        current: PersistencePort = app.state.persistence
        queued = ContinuousAgentSteeringQueue(
            current,
            project_key=key,
            session_id=session_id,
        ).enqueue(
            body.message,
            client_steering_id=body.client_steering_id,
        )
        return {
            "status": "queued",
            "project": project,
            "session_id": session_id,
            "turn_id": entry.get("turn_id"),
            "steering_id": queued.steering_id,
        }

    @app.post("/api/conversations/{project}/cancel", status_code=202)
    def cancel_continuous_agent(
        project: str,
        body: WebCancelRequest,
    ) -> dict[str, object]:
        resolve_project(project, body.root_index)
        key = _task_key(body.root_index, project)
        session_id, entry = _active_continuous_session(
            project_key=key,
            requested_session_id=body.session_id,
        )
        cancel_event = entry.get("cancel_event")
        if cancel_event is None or not callable(getattr(cancel_event, "set", None)):
            raise HTTPException(
                status_code=409,
                detail="当前 Agent Turn 不支持进程内取消",
            )
        cancel_event.set()  # type: ignore[union-attr]
        return {
            "status": "cancellation_requested",
            "project": project,
            "session_id": session_id,
            "turn_id": entry.get("turn_id"),
        }

    @app.get("/api/projects/{project}/memories")
    def list_project_memories(
        project: str,
        root_index: int = 0,
        memory_type: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        if not 1 <= limit <= 100:
            raise HTTPException(status_code=422, detail="limit 无效")
        current: PersistencePort = app.state.persistence
        memories = current.find_memories(
            _task_key(root_index, project),
            memory_type=memory_type,
            limit=limit,
        )
        return {
            "project": project,
            "durable": current.durable,
            "memories": [
                {
                    "key": item.memory_key,
                    "memory_type": item.memory_type,
                    "value": item.value,
                    "confidence": item.confidence,
                    "source_thread_id": item.source_thread_id,
                }
                for item in memories
            ],
        }

    @app.put("/api/projects/{project}/memories")
    def put_project_memory(
        project: str,
        body: WebMemoryUpsert,
    ) -> dict[str, object]:
        resolve_project(project, body.root_index)
        current: PersistencePort = app.state.persistence
        current.upsert_memory(
            project_key=_task_key(body.root_index, project),
            memory_key=body.key,
            memory_type=body.memory_type,
            value=body.value,
            confidence=body.confidence,
        )
        return {"status": "ok", "project": project, "durable": current.durable}

    @app.post("/api/projects/{project}/knowledge/documents")
    def ingest_knowledge_document(
        project: str,
        body: WebKnowledgeIngest,
    ) -> dict[str, object]:
        resolve_project(project, body.root_index)
        service: KnowledgeService | None = app.state.knowledge_service
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="知识库需要 LanceDB 和 embedding 配置",
            )
        document = service.ingest(
            project_key=_task_key(body.root_index, project),
            source_uri=body.source_uri,
            title=body.title,
            content=body.content,
            metadata=body.metadata,
        )
        return {
            "status": "ok",
            "document_id": document.document_id,
            "chunks": document.chunks,
            "content_hash": document.content_hash,
        }

    @app.post("/api/projects/{project}/knowledge/search")
    def search_knowledge(
        project: str,
        body: WebKnowledgeSearch,
    ) -> dict[str, object]:
        resolve_project(project, body.root_index)
        service: KnowledgeService | None = app.state.knowledge_service
        if service is None:
            raise HTTPException(status_code=503, detail="知识库尚未配置")
        matches = service.search(
            project_key=_task_key(body.root_index, project),
            query=body.query,
            limit=body.limit,
        )
        return {
            "matches": [
                {
                    "document_id": item.document_id,
                    "title": item.title,
                    "source_uri": item.source_uri,
                    "ordinal": item.ordinal,
                    "content": item.content,
                    "score": item.score,
                    "knowledge_id": getattr(item, "knowledge_id", None),
                    "subject": getattr(item, "subject", None),
                    "category": getattr(item, "category", None),
                    "source_pages": list(getattr(item, "source_pages", ())),
                    "source_section": getattr(item, "source_section", None),
                    "applicable_conditions": list(
                        getattr(item, "applicable_conditions", ())
                    ),
                    "limitations": list(getattr(item, "limitations", ())),
                }
                for item in matches
            ]
        }

    @app.get("/api/projects/{project}/knowledge/documents")
    def list_knowledge_documents(project: str, root_index: int = 0) -> dict[str, object]:
        resolve_project(project, root_index)
        service: KnowledgeService | None = app.state.knowledge_service
        if service is None:
            raise HTTPException(status_code=503, detail="知识库尚未配置")
        return {"documents": service.list_documents(_task_key(root_index, project))}

    @app.get("/api/projects/{project}/knowledge/documents/{document_id}")
    def get_knowledge_document(
        project: str, document_id: str, root_index: int = 0
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        service: KnowledgeService | None = app.state.knowledge_service
        if service is None:
            raise HTTPException(status_code=503, detail="知识库尚未配置")
        document = service.get_document(
            project_key=_task_key(root_index, project), document_id=document_id
        )
        if document is None:
            raise HTTPException(status_code=404, detail="知识文档不存在")
        return document

    @app.delete("/api/projects/{project}/knowledge/documents/{document_id}")
    def delete_knowledge_document(
        project: str, document_id: str, root_index: int = 0
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        service: KnowledgeService | None = app.state.knowledge_service
        if service is None:
            raise HTTPException(status_code=503, detail="知识库尚未配置")
        deleted = service.delete_document(
            project_key=_task_key(root_index, project), document_id=document_id
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="知识文档不存在")
        return {"status": "ok", "deleted": True, "document_id": document_id}

    @app.post("/api/projects/{project}/knowledge/import-pdf")
    def import_knowledge_pdf(
        project: str, body: WebKnowledgePdfImport
    ) -> dict[str, object]:
        project_path = resolve_project(project, body.root_index).resolve()
        candidate = (project_path / body.relative_path).resolve()
        try:
            candidate.relative_to(project_path)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="PDF 必须位于当前项目目录内") from error
        service: KnowledgeService | None = app.state.knowledge_service
        if service is None:
            raise HTTPException(status_code=503, detail="知识库尚未配置")
        imported = service.ingest_pdf(
            project_key=_task_key(body.root_index, project),
            source_uri=body.relative_path.replace("\\", "/"),
            title=body.title or candidate.stem,
            path=candidate,
            reader=_configured_pdf_reader(),
        )
        return {
            "status": "ok",
            "source_uri": imported.source_uri,
            "total_pages": imported.total_pages,
            "batches": imported.batches,
            "sections": imported.batches,
            "knowledge_units": imported.knowledge_units,
            "document_ids": [item.document_id for item in imported.documents],
        }

    @app.post("/api/conversations/{project}")
    def run_task(
        project: str,
        body: WebTaskRequest,
    ) -> StreamingResponse:
        _root_index(body.root_index)
        project_path = resolve_project(project, body.root_index)
        project_target_chip = catalogs[body.root_index].target_chip(project)
        task_key = _task_key(body.root_index, project)
        current: PersistencePort = app.state.persistence
        explicit_pdf_read = is_explicit_pdf_read_request(body.message)
        if _continuous_project_enabled(task_key) and not explicit_pdf_read:
            if (
                project_target_chip is not None
                and body.target_chip is not None
                and body.target_chip != project_target_chip
            ):
                raise HTTPException(
                    status_code=409,
                    detail="项目芯片已固定，不能在任务中更改",
                )
            _validate_task_port(body.serial_port)
            resolved_serial_port = body.serial_port or serial_port
            resolved_target_chip = (
                project_target_chip or body.target_chip or target_chip
            )
            selected_checkpointer: BaseCheckpointSaver | None = (
                app.state.checkpointer
            )
            if selected_checkpointer is None:
                raise HTTPException(
                    status_code=503,
                    detail="持续 Agent checkpoint 存储尚未初始化",
                )
            identity: ContinuousAgentTurnIdentity | None = None
            cancel_event: threading.Event | None = None
            runtime_handed_off = False
            try:
                with continuous_active_lock:
                    requested_or_active = body.session_id
                    if requested_or_active is None:
                        active_session = current.get_active_agent_session(task_key)
                        requested_or_active = (
                            active_session.session_id
                            if active_session is not None
                            else None
                        )
                    if (
                        requested_or_active is not None
                        and requested_or_active in continuous_active_sessions
                    ):
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "该 Agent Session 正在运行；请使用 /steer 追加指令，"
                                "或使用 /cancel 请求安全停止"
                            ),
                        )
                    identity = begin_continuous_agent_turn(
                        current,
                        project_key=task_key,
                        user_message=body.message,
                        requested_session_id=body.session_id,
                        client_turn_id=body.client_turn_id,
                    )
                    if not identity.replayed:
                        cancel_event = threading.Event()
                        continuous_active_sessions[
                            identity.session.session_id
                        ] = {
                            "project_key": task_key,
                            "turn_id": identity.turn.turn_id,
                            "cancel_event": cancel_event,
                        }
                if identity.replayed:
                    return run_continuous_agent_http_turn(
                        project_name=project,
                        root_index=body.root_index,
                        project_key=task_key,
                        message=body.message,
                        requested_session_id=body.session_id,
                        client_turn_id=body.client_turn_id,
                        resolved_inputs={},
                        max_steps=max(20, body.max_attempts * 10),
                        context=None,
                        persistence=current,
                        checkpointer=selected_checkpointer,
                        identity=identity,
                    )
                steering_queue = ContinuousAgentSteeringQueue(
                    current,
                    project_key=task_key,
                    session_id=identity.session.session_id,
                )
                continuous_context = _continuous_agent_context(
                    project_path=project_path,
                    target_chip=resolved_target_chip,
                    selected_serial_port=resolved_serial_port,
                    allow_dependency_downloads=(
                        body.allow_dependency_downloads
                    ),
                    persistence=current,
                    drain_steering=steering_queue.drain,
                    cancellation_requested=(
                        cancel_event.is_set if cancel_event is not None else None
                    ),
                )
                def finish_continuous_runtime() -> None:
                    with continuous_active_lock:
                        continuous_active_sessions.pop(
                            identity.session.session_id,
                            None,
                        )

                response = run_continuous_agent_http_turn(
                    project_name=project,
                    root_index=body.root_index,
                    project_key=task_key,
                    message=body.message,
                    requested_session_id=body.session_id,
                    client_turn_id=body.client_turn_id,
                    resolved_inputs={
                        key: value
                        for key, value in {
                            "serial_port": resolved_serial_port,
                            "target_chip": resolved_target_chip,
                        }.items()
                        if value is not None
                    },
                    max_steps=max(20, body.max_attempts * 10),
                    context=continuous_context,
                    persistence=current,
                    checkpointer=selected_checkpointer,
                    runtime_metadata={
                        "serial_port": resolved_serial_port,
                        "target_chip": resolved_target_chip,
                        "allow_dependency_downloads": (
                            body.allow_dependency_downloads
                        ),
                    },
                    identity=identity,
                    on_complete=finish_continuous_runtime,
                )
                runtime_handed_off = True
                return response
            except CapabilityError as error:
                details = {
                    "authentication": (
                        "持续 Agent 模型认证失败，请检查对应厂商的 API Key"
                    ),
                    "timeout": "持续 Agent 模型请求超时，请稍后重试",
                    "rate_limit": "持续 Agent 模型请求过于频繁或额度不足",
                    "service": "持续 Agent 模型服务当前不可用",
                    "empty_response": "持续 Agent 模型没有返回内容",
                    "invalid_json": "持续 Agent 模型返回的内容不是有效 JSON",
                    "invalid_schema": (
                        "持续 Agent 模型返回的数据不符合 AgentStep 协议"
                    ),
                }
                raise HTTPException(
                    status_code=503,
                    detail=details.get(error.category, error.message),
                ) from error
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            finally:
                if (
                    identity is not None
                    and not identity.replayed
                    and not runtime_handed_off
                ):
                    unfinished_turn = current.get_agent_turn(identity.turn.turn_id)
                    if (
                        unfinished_turn is not None
                        and unfinished_turn.status == "running"
                    ):
                        current.finish_agent_turn(
                            identity.turn.turn_id,
                            status="failed",
                            assistant_message=(
                                "持续 Agent Turn 在生成最终结果前异常终止。"
                            ),
                            failure={
                                "category": "runtime",
                                "code": "turn_aborted_before_projection",
                                "message": (
                                    "持续 Agent Turn 在生成最终结果前异常终止"
                                ),
                            },
                        )
                    with continuous_active_lock:
                        continuous_active_sessions.pop(
                            identity.session.session_id,
                            None,
                        )
        latest_run = current.get_latest_run(task_key)
        previous_run = latest_run or current.get_latest_completed_run(task_key)
        previous_context = _run_context(previous_run)
        retry_context = (
            previous_context
            if previous_context is not None
            and previous_context.get("status") in {"blocked", "failed"}
            and _is_retry_directive(body.message)
            else None
        )
        knowledge_service: KnowledgeService | None = app.state.knowledge_service
        if knowledge_service is None:
            knowledge_status = (
                "项目外部知识库未启用（缺少 embedding 配置），"
                "当前没有可检索的项目 LanceDB 知识文档。ESP-IDF SDK 例程知识库"
                "是独立作用域，不能把其中内容当作当前项目资料。"
                "本地 PDF 分批读取器仍然可用；读取 PDF 不依赖外部知识库，"
                "只有把读取结果写入 RAG 才需要 embedding 配置。"
            )
        else:
            try:
                count = knowledge_service.document_count(task_key)
                knowledge_status = (
                    "项目外部知识库已启用，且当前项目已有知识文档。"
                    if count > 0
                    else "项目外部知识库已启用，但当前项目没有任何知识文档（为空）。"
                )
            except Exception:
                knowledge_status = (
                    "项目外部知识库已启用，但文档数量暂时无法确认；"
                    "不要声称其中存在任何具体资料。"
                )
        context_compaction_notice = ""
        try:
            conversation_history = current.get_messages(task_key)
            if isinstance(selected_conversation_router, DeepSeekConversationRouter):
                summary = ""
                covered_message_count = 0
                summaries = current.find_memories(
                    task_key,
                    memory_type="conversation_context",
                    limit=20,
                )
                stored_summary = next(
                    (
                        item
                        for item in summaries
                        if item.memory_key == _CONVERSATION_SUMMARY_KEY
                    ),
                    None,
                )
                if stored_summary is not None:
                    summary = str(stored_summary.value.get("summary", ""))
                    covered_message_count = int(
                        stored_summary.value.get("covered_message_count", 0)
                    )
                prepared_context = selected_conversation_router.prepare_history(
                    body.message,
                    conversation_history,
                    summary=summary,
                    covered_message_count=covered_message_count,
                    previous_run=previous_context,
                )
                conversation_history = prepared_context.history
                if prepared_context.compacted:
                    context_compaction_notice = (
                        "对话上下文已达到模型窗口的 95%，我已将早期对话压缩为"
                        "滚动摘要，并保留最近的原始消息。\n\n"
                    )
                    current.upsert_memory(
                        project_key=task_key,
                        memory_key=_CONVERSATION_SUMMARY_KEY,
                        memory_type="conversation_context",
                        value={
                            "summary": prepared_context.summary,
                            "covered_message_count": (
                                prepared_context.covered_message_count
                            ),
                            "estimated_tokens": prepared_context.estimated_tokens,
                            "context_window_tokens": (
                                prepared_context.context_window_tokens
                            ),
                            "compaction_threshold": 0.95,
                        },
                    )
            decision = (
                ConversationDecision(intent="firmware_task")
                if retry_context is not None
                else ConversationDecision(intent="knowledge_task")
                if explicit_pdf_read
                else selected_conversation_router.route(
                    body.message,
                    conversation_history,
                    knowledge_status=knowledge_status,
                    previous_run=previous_context,
                )
                if selected_conversation_router is not None
                else None
            )
            if explicit_pdf_read:
                # Enforce the same safety/capability boundary for injected or
                # older routers: an explicit read-only PDF command belongs to
                # the dedicated document workflow.
                decision = ConversationDecision(intent="knowledge_task")
            elif is_explicit_firmware_command(body.message):
                # Keep injected routers and model fallbacks consistent with the
                # production router for this high-confidence external action.
                decision = ConversationDecision(intent="firmware_task")
            elif is_display_diagnosis_request(body.message):
                # A blank display is a project-diagnosis request even when an
                # injected router or a model classifies the question as chat.
                decision = ConversationDecision(intent="project_inspection")
        except CapabilityError as error:
            details = {
                "authentication": "对话模型认证失败，请检查对应厂商的 API Key",
                "timeout": "对话模型请求超时，请检查模型服务状态或增大超时时间",
                "rate_limit": "对话模型请求过于频繁或额度不足，请稍后重试",
                "service": (
                    "对话模型服务拒绝或无法处理请求，请检查 Base URL、"
                    "模型名称以及 OpenAI 兼容接口"
                ),
                "empty_response": "对话模型没有返回内容",
                "invalid_json": "对话模型返回的内容不是有效 JSON",
                "invalid_schema": "对话模型返回的数据不符合 LUXAR 路由格式",
            }
            raise HTTPException(
                status_code=503,
                detail=details[error.category],
            ) from error

        _observe_continuous_agent_shadow(
            project_path=project_path,
            project_key=task_key,
            user_message=body.message,
            client_turn_id=body.client_turn_id,
            legacy_intent=(
                decision.intent if decision is not None else "firmware_task"
            ),
            selected_serial_port=body.serial_port or serial_port,
            selected_target_chip=(
                project_target_chip or body.target_chip or target_chip
            ),
            allow_dependency_downloads=body.allow_dependency_downloads,
            persistence=current,
        )

        continuous_turn: ContinuousAgentTurnIdentity | None = None
        if _continuous_project_enabled(task_key):
            try:
                continuous_turn = begin_continuous_agent_turn(
                    current,
                    project_key=task_key,
                    user_message=body.message,
                    requested_session_id=body.session_id,
                    client_turn_id=body.client_turn_id,
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        def conversation_headers(stream_id: str) -> dict[str, str]:
            headers = {
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-LUXAR-Thread-ID": stream_id,
            }
            if continuous_turn is not None:
                headers.update(
                    {
                        "X-LUXAR-Session-ID": (
                            continuous_turn.session.session_id
                        ),
                        "X-LUXAR-Turn-ID": continuous_turn.turn.turn_id,
                    }
                )
            return headers

        if continuous_turn is not None and continuous_turn.replayed:
            stored_stream = current.get_conversation_stream(
                continuous_turn.turn.turn_id
            )
            replay_text = continuous_turn.turn.assistant_message
            if stored_stream is not None and stored_stream.assistant_content:
                replay_text = stored_stream.assistant_content

            def replay_turn_stream() -> Generator[str, None, None]:
                yield _sse_event(
                    "turn_status",
                    {
                        "status": continuous_turn.turn.status,
                        "replayed": True,
                    },
                )
                for chunk in _text_chunks(replay_text):
                    yield _sse_event("token", {"token": chunk})
                yield _sse_event("done", "[DONE]")

            return StreamingResponse(
                replay_turn_stream(),
                media_type="text/event-stream",
                headers=conversation_headers(continuous_turn.turn.turn_id),
            )
        direct_focused_reply = (
            decision is not None
            and decision.intent in {"knowledge_task", "project_inspection"}
            and decision.response_plan is not None
            and decision.response_plan.operation in {"direct_answer", "clarify"}
        )
        if decision is not None and (
            decision.intent in {"casual_chat", "workflow_status"}
            or direct_focused_reply
        ):
            thread_id = (
                continuous_turn.turn.turn_id
                if continuous_turn is not None
                else uuid.uuid4().hex
            )
            response_text = context_compaction_notice + decision.response
            current.append_exchange(
                task_key,
                thread_id=thread_id,
                user_message=body.message,
                assistant_message=response_text,
            )
            if continuous_turn is not None:
                current.finish_agent_turn(
                    thread_id,
                    status="completed",
                    assistant_message=response_text,
                )

            def chat_stream() -> Generator[str, None, None]:
                for chunk in _text_chunks(response_text):
                    yield _sse_event("token", {"token": chunk})
                    time.sleep(0.01)
                yield _sse_event("done", "[DONE]")

            return StreamingResponse(
                chat_stream(),
                media_type="text/event-stream",
                headers=conversation_headers(thread_id),
            )

        if (
            decision is not None
            and decision.intent == "firmware_task"
            and is_explicit_firmware_command(body.message)
            and production_bootstrap
            and production_agent_bootstrap
            and not (body.serial_port or serial_port)
        ):
            thread_id = (
                continuous_turn.turn.turn_id
                if continuous_turn is not None
                else uuid.uuid4().hex
            )
            response_text = (
                "可以烧录，但还缺少开发板串口。请在本次请求中选择或填写串口，"
                "例如 COM3；收到串口后我会先构建，再请求烧录确认并执行。"
            )
            current.append_exchange(
                task_key,
                thread_id=thread_id,
                user_message=body.message,
                assistant_message=response_text,
            )
            if continuous_turn is not None:
                current.finish_agent_turn(
                    thread_id,
                    status="waiting_input",
                    assistant_message=response_text,
                    failure={
                        "category": "user_input",
                        "missing": ["serial_port"],
                    },
                )

            def missing_flash_port_stream() -> Generator[str, None, None]:
                for chunk in _text_chunks(response_text):
                    yield _sse_event("token", {"token": chunk})
                    time.sleep(0.01)
                yield _sse_event("done", "[DONE]")

            return StreamingResponse(
                missing_flash_port_stream(),
                media_type="text/event-stream",
                headers=conversation_headers(thread_id),
            )

        if (
            project_target_chip is not None
            and body.target_chip is not None
            and body.target_chip != project_target_chip
        ):
            raise HTTPException(
                status_code=409,
                detail="项目芯片已固定，不能在任务中更改",
            )
        _validate_task_port(body.serial_port)
        task_mode: TaskExecutionMode = (
            "inspection"
            if decision is not None and decision.intent == "project_inspection"
            else "knowledge"
            if decision is not None and decision.intent == "knowledge_task"
            else "firmware"
        )
        runtime_dispatch = dispatch_runtime(task_mode, selected_runtime)
        use_supervisor = runtime_dispatch.uses_supervisor
        use_specialized = task_mode in {"inspection", "knowledge"}
        production_firmware_bootstrap = (
            production_agent_bootstrap
            if use_supervisor
            else production_bootstrap and not use_specialized
        )
        idf_command = (
            _require_idf_command()
            if production_firmware_bootstrap and task_mode == "firmware"
            else None
        )
        active_idf_path = (
            selected_toolchain_manager.status.idf_path
            if production_firmware_bootstrap and task_mode == "firmware"
            else None
        )
        # 页面选择优先,未选择时回退到服务端默认。
        resolved_serial_port = body.serial_port or serial_port
        resolved_target_chip = (
            project_target_chip or body.target_chip or target_chip
        )
        thread_id = (
            continuous_turn.turn.turn_id
            if continuous_turn is not None
            else uuid.uuid4().hex
        )
        workflow_thread_id = (
            continuous_turn.session.session_id
            if continuous_turn is not None
            else thread_id
        )
        runtime_config: dict[str, object] = {
            "root_index": body.root_index,
            "project_name": project,
            "serial_port": resolved_serial_port,
            "target_chip": resolved_target_chip,
            "allow_dependency_downloads": body.allow_dependency_downloads,
            "task_text": (
                _retry_task_text(retry_context)
                if retry_context is not None
                else body.message
            ),
            "source_message": body.message,
            "retry_of_thread_id": (
                previous_run.thread_id if retry_context is not None else None
            ),
            "agent_runtime": (
                "supervisor" if use_supervisor else "legacy"
            ),
            "firmware_runtime": runtime_dispatch.firmware_runtime,
            "firmware_runtime_reason": (
                runtime_dispatch.firmware_runtime_reason
            ),
            "workflow_family": runtime_dispatch.workflow_family,
            "continuous_agent_v2": _continuous_project_enabled(task_key),
            "session_id": (
                continuous_turn.session.session_id
                if continuous_turn is not None
                else None
            ),
            "turn_id": thread_id,
        }
        effective_task_text = str(runtime_config["task_text"])

        with active_lock:
            if task_key in active_projects:
                raise HTTPException(
                    status_code=409,
                    detail="该项目已有正在运行的任务",
                )
            if not capacity.acquire(blocking=False):
                raise HTTPException(
                    status_code=429,
                    detail="当前运行任务过多，请稍后重试",
                )
            active_projects.add(task_key)
        try:
            current.start_conversation_stream(
                thread_id=thread_id,
                task_key=task_key,
                user_message=body.message,
            )
        except Exception as error:
            with active_lock:
                active_projects.discard(task_key)
                capacity.release()
            logger.exception("Failed to initialize conversation stream %s", thread_id)
            raise HTTPException(
                status_code=500,
                detail="无法初始化可恢复的任务输出流",
            ) from error

        def event_stream() -> Generator[str, None, None]:
            events: queue.Queue[_StreamItem] = queue.Queue(maxsize=64)
            disconnected = threading.Event()
            conversation_parts: list[str] = []

            def publish(event: str, data: dict[str, object] | str) -> None:
                try:
                    current.append_conversation_stream_event(
                        thread_id,
                        event=event,
                        data=data,
                    )
                except Exception:
                    logger.exception(
                        "Failed to persist conversation stream event %s for %s",
                        event,
                        thread_id,
                    )
                if disconnected.is_set():
                    return
                while not disconnected.is_set():
                    try:
                        events.put((event, data), timeout=0.1)
                        return
                    except queue.Full:
                        continue

            def publish_text(content: str) -> None:
                conversation_parts.append(content)
                for chunk in _text_chunks(content):
                    publish("token", {"token": chunk})
                    time.sleep(0.01)

            heartbeat_progress_lock = threading.Lock()
            heartbeat_progress: dict[str, object] = {
                "stage": "workflow_runtime",
                "tools": [],
                "task_id": None,
            }

            def report(progress: WorkflowProgress) -> None:
                progress_payload: dict[str, object] = {
                    "stage": progress.stage,
                    "message": progress.message,
                    "attempts": progress.attempts,
                }
                for field in (
                    "progress_type",
                    "current",
                    "total",
                    "unit",
                    "phase",
                    "batch",
                ):
                    value = getattr(progress, field, None)
                    if value is not None:
                        progress_payload[field] = value
                publish(
                    "progress",
                    progress_payload,
                )
                # 专用工作流的 narrative 是内部解释，不是给用户的答案。
                # 进度通过结构化 progress 事件展示，聊天正文只发布最终结果。
                if not use_specialized and progress.narrative:
                    publish_text(progress.narrative)

            def report_agent(progress: AgentWorkflowProgress) -> None:
                # Supervisor 的逐轮决策属于工作台运行状态，不是对用户的回复。
                # 结构化 progress 事件足以驱动状态栏，持久化的 Agent snapshot
                # 则为“Agent 工作台”提供目标、任务图、当前任务和证据详情。
                progress_payload: dict[str, object] = {
                        "stage": progress.node,
                        "message": progress.message,
                        "attempts": progress.step_count,
                        "phase": progress.phase,
                        "tools": list(progress.tools),
                        "task_id": progress.task_id,
                    }
                if progress.detail:
                    progress_payload["detail"] = progress.detail
                with heartbeat_progress_lock:
                    heartbeat_progress["stage"] = progress.node
                    heartbeat_progress["tools"] = list(progress.tools)
                    heartbeat_progress["task_id"] = progress.task_id
                publish("progress", progress_payload)

            @contextmanager
            def workflow_heartbeat() -> Generator[None, None, None]:
                """在同步 LangGraph/硬件调用期间维持 SSE 活性。"""

                stopped = threading.Event()

                def heartbeat_worker() -> None:
                    while not stopped.wait(_WORKFLOW_HEARTBEAT_SECONDS):
                        with heartbeat_progress_lock:
                            stage = str(heartbeat_progress["stage"])
                            tools = list(heartbeat_progress["tools"])
                            task_id = heartbeat_progress["task_id"]
                        publish(
                            "progress",
                            {
                                "stage": stage,
                                "message": "任务仍在执行，正在等待工具返回结果",
                                "attempts": 0,
                                "phase": "heartbeat",
                                "tools": tools,
                                "task_id": task_id,
                            },
                        )

                heartbeat = threading.Thread(
                    target=heartbeat_worker,
                    name=f"luxar-heartbeat-{thread_id[:8]}",
                    daemon=True,
                )
                heartbeat.start()
                try:
                    yield
                finally:
                    stopped.set()
                    heartbeat.join(timeout=1.0)

            def worker() -> None:
                phase = "persistence"
                run_started = False
                stream_terminal_status = "failed"
                exchange_persisted = False
                failure_message = "任务执行异常，请检查服务端日志"

                def finish_failed_run(
                    current: PersistencePort,
                    *,
                    category: str,
                    message: str,
                ) -> None:
                    if not run_started:
                        return
                    try:
                        current.finish_run(
                            thread_id,
                            status="failed",
                            result={
                                "status": "failed",
                                "error": {
                                    "category": category,
                                    "message": message,
                                },
                            },
                        )
                    except Exception:
                        logger.exception(
                            "Failed to persist failed workflow run %s",
                            thread_id,
                        )

                try:
                    current: PersistencePort = app.state.persistence
                    current.start_run(
                        thread_id=thread_id,
                        task_key=task_key,
                        project_name=project,
                        root_index=body.root_index,
                        task_text=effective_task_text,
                        runtime_config=runtime_config,
                    )
                    run_started = True
                    phase = "startup"
                    if use_supervisor:
                        agent_bootstrap_options: dict[str, object] = {
                            "project_path": project_path,
                            "allow_dependency_downloads": (
                                body.allow_dependency_downloads
                            ),
                            "serial_port": resolved_serial_port,
                        }
                        if idf_command is not None:
                            agent_bootstrap_options["idf_command"] = idf_command
                        if production_agent_bootstrap:
                            agent_bootstrap_options["settings"] = (
                                selected_model_store.load().conversation.resolved()
                            )
                        context = agent_bootstrap_factory(
                            **agent_bootstrap_options
                        )
                    elif use_specialized:
                        specialized_options: dict[str, object] = {
                            "project_path": project_path,
                            "target_chip": resolved_target_chip,
                            "persistence": current,
                            "project_key": task_key,
                        }
                        if production_specialized_bootstrap:
                            specialized_options["settings"] = (
                                selected_model_store.load().conversation.resolved()
                            )
                            specialized_options["document_reader"] = (
                                _configured_pdf_reader()
                            )
                        if current.durable:
                            specialized_options.update(
                                {
                                    "checkpointer": app.state.checkpointer,
                                    "knowledge_service": (
                                        app.state.knowledge_service
                                    ),
                                }
                            )
                        context = selected_specialized_bootstrap(
                            **specialized_options
                        )
                    else:
                        bootstrap_options: dict[str, object] = {
                            "project_path": project_path,
                            "allow_dependency_downloads": (
                                body.allow_dependency_downloads
                            ),
                            "serial_port": resolved_serial_port,
                            "target_chip": resolved_target_chip,
                        }
                        if idf_command is not None:
                            bootstrap_options["idf_command"] = idf_command
                        if active_idf_path is not None:
                            bootstrap_options["idf_path"] = Path(active_idf_path)
                        if production_bootstrap:
                            bootstrap_options["settings"] = (
                                selected_model_store.load().conversation.resolved()
                            )
                            bootstrap_options["document_reader"] = (
                                _configured_pdf_reader()
                            )
                        if current.durable:
                            bootstrap_options.update(
                                {
                                    "checkpointer": app.state.checkpointer,
                                    "persistence": current,
                                    "project_key": task_key,
                                    "knowledge_service": (
                                        app.state.knowledge_service
                                    ),
                                    "sdk_example_knowledge": (
                                        app.state.sdk_example_knowledge
                                    ),
                                }
                            )
                        context = bootstrap_factory(**bootstrap_options)
                    phase = "workflow"
                    if context_compaction_notice:
                        publish_text(context_compaction_notice)
                    if retry_context is not None:
                        previous_error = str(
                            retry_context.get("last_error", "未知失败原因")
                        )
                        publish_text(
                            "已识别“重试”为承接上一任务的指令。"
                            f"上一任务是“{retry_context.get('task_text', '')}”，"
                            f"上次失败原因为“{previous_error}”。"
                            "我会保留当前源码和原目标，重新检查后从失败处修复。\n\n"
                        )
                    publish_text(
                        (
                            "我先读取并分析当前项目代码，再报告实际功能、结构和缺失项。\n\n"
                            if task_mode == "inspection"
                            else (
                                (
                                    "正在读取你指定的本地 PDF，并提取与当前需求相关的"
                                    "技术信息。\n\n"
                                    if explicit_pdf_read
                                    else (
                                        "我先分析问题并检索项目知识库；如果需要读取完整文档，"
                                        "会分批提取其中可复用的具体知识。\n\n"
                                    )
                                )
                                if task_mode == "knowledge"
                                else (
                                    "我先核对需求和当前项目代码，再决定是复用、"
                                    "修改还是从零创建。\n\n"
                                )
                            )
                        )
                    )
                    if use_supervisor:
                        retry_seed: dict[str, object] = {}
                        if retry_context is not None:
                            restored = load_agent_snapshot(current, task_key)
                            retry_seed = {
                                field: restored[field]
                                for field in (
                                    "objective",
                                    "change_set",
                                    "capabilities",
                                    "build_evidence",
                                    "build_recovery",
                                )
                                if field in restored
                            }
                        with workflow_heartbeat():
                            agent_run_result = agent_workflow_runner(
                                initial_state={
                                    **retry_seed,
                                    "task_text": effective_task_text,
                                    "source_message_id": thread_id,
                                    "project_name": project,
                                    "target_chip": resolved_target_chip,
                                    "trace": [],
                                    "max_steps": max(20, body.max_attempts * 10),
                                },
                                context=context,
                                progress_reporter=report_agent,
                                thread_id=workflow_thread_id,
                                checkpointer=app.state.checkpointer,
                                persistence=current,
                                project_key=task_key,
                            )
                        phase = "result"
                        while agent_run_result.pending_approval is not None:
                            request = agent_run_result.pending_approval
                            decision_event = threading.Event()
                            entry = {
                                "thread_id": agent_run_result.thread_id,
                                "request": request.model_dump(mode="json"),
                                "event": decision_event,
                                "approved": False,
                                "feedback": "",
                                "selected_option": None,
                            }
                            current.save_pending_approval(
                                PendingApprovalRecord(
                                    task_key=task_key,
                                    project_name=project,
                                    root_index=body.root_index,
                                    thread_id=agent_run_result.thread_id,
                                    request=request.model_dump(mode="json"),
                                    runtime_config=runtime_config,
                                )
                            )
                            with pending_lock:
                                pending_approvals[task_key] = entry
                            current.finish_conversation_stream(
                                thread_id, status="pending_approval"
                            )
                            publish(
                                "approval",
                                {
                                    "thread_id": agent_run_result.thread_id,
                                    "request": entry["request"],
                                },
                            )

                            decision_event.wait()
                            with pending_lock:
                                pending_approvals.pop(task_key, None)
                            current.finish_conversation_stream(
                                thread_id, status="running"
                            )
                            if agent_run_result.checkpointer is None:
                                raise RuntimeError(
                                    "Supervisor approval checkpoint is unavailable"
                                )
                            # 用户决策已被消费；后续即使工具运行较久，也不能再把
                            # 数据库状态展示成“等待用户审批”。
                            current.complete_approval(task_key)
                            with workflow_heartbeat():
                                agent_run_result = agent_workflow_resumer(
                                    thread_id=agent_run_result.thread_id,
                                    context=context,
                                    checkpointer=agent_run_result.checkpointer,
                                    approved=bool(entry["approved"]),
                                    feedback=str(entry.get("feedback", "")),
                                    selected_option=(
                                        str(entry["selected_option"])
                                        if entry.get("selected_option") is not None
                                        else None
                                    ),
                                    progress_reporter=report_agent,
                                    persistence=current,
                                    project_key=task_key,
                                )
                        final_thread_id = agent_run_result.thread_id
                        envelope = agent_state_to_result(
                            agent_run_result.state
                        )
                        assistant_message = agent_user_message_for_state(
                            agent_run_result.state
                        )
                        live_message = assistant_message
                    elif use_specialized:
                        specialized_result = selected_specialized_runner(
                            initial_state=SpecializedWorkflowState(
                                task_text=effective_task_text,
                                task_mode=task_mode,
                                response_plan=(
                                    decision.response_plan.model_dump(mode="json")
                                    if decision is not None
                                    and decision.response_plan is not None
                                    else {}
                                ),
                                conversation_context=[
                                    {
                                        "role": item.get("role", ""),
                                        "content": str(item.get("content", ""))[-2000:],
                                    }
                                    for item in conversation_history[-8:]
                                    if item.get("role") in {"user", "assistant"}
                                ],
                                trace=[],
                            ),
                            context=context,
                            progress_reporter=report,
                            thread_id=workflow_thread_id,
                        )
                        phase = "result"
                        while specialized_result.pending_approval is not None:
                            request = specialized_result.pending_approval
                            decision_event = threading.Event()
                            entry = {
                                "thread_id": specialized_result.thread_id,
                                "request": request.model_dump(mode="json"),
                                "event": decision_event,
                                "approved": False,
                                "feedback": "",
                                "selected_option": None,
                            }
                            current.save_pending_approval(
                                PendingApprovalRecord(
                                    task_key=task_key,
                                    project_name=project,
                                    root_index=body.root_index,
                                    thread_id=specialized_result.thread_id,
                                    request=request.model_dump(mode="json"),
                                    runtime_config=runtime_config,
                                )
                            )
                            with pending_lock:
                                pending_approvals[task_key] = entry
                            current.finish_conversation_stream(
                                thread_id, status="pending_approval"
                            )
                            publish(
                                "approval",
                                {
                                    "thread_id": specialized_result.thread_id,
                                    "request": entry["request"],
                                },
                            )
                            decision_event.wait()
                            with pending_lock:
                                pending_approvals.pop(task_key, None)
                            current.finish_conversation_stream(
                                thread_id, status="running"
                            )
                            specialized_result = selected_specialized_resumer(
                                thread_id=specialized_result.thread_id,
                                context=context,
                                approved=bool(entry["approved"]),
                                feedback=str(entry.get("feedback", "")),
                                selected_option=(
                                    str(entry["selected_option"])
                                    if entry.get("selected_option") is not None
                                    else None
                                ),
                                progress_reporter=report,
                            )
                            current.complete_approval(task_key)
                        final_thread_id = specialized_result.thread_id
                        envelope = specialized_state_to_result(
                            specialized_result.state
                        )
                        assistant_message = specialized_user_message(
                            specialized_result.state
                        )
                        live_message = assistant_message
                    else:
                        # Web 不注入审批回调：烧录前工作流暂停并发布审批事件。
                        run_result = workflow_runner(
                            initial_state=WorkflowState(
                                task_text=effective_task_text,
                                task_mode=task_mode,
                                attempts=0,
                                max_attempts=body.max_attempts,
                                trace=[],
                            ),
                            context=context,
                            progress_reporter=report,
                            thread_id=workflow_thread_id,
                        )
                        phase = "result"

                        while run_result.pending_approval is not None:
                            request = run_result.pending_approval
                            decision_event = threading.Event()
                            entry: dict[str, object] = {
                                "thread_id": run_result.thread_id,
                                "request": request.model_dump(mode="json"),
                                "event": decision_event,
                                "approved": False,
                                "feedback": "",
                                "selected_option": None,
                            }
                            current.save_pending_approval(
                                PendingApprovalRecord(
                                    task_key=task_key,
                                    project_name=project,
                                    root_index=body.root_index,
                                    thread_id=run_result.thread_id,
                                    request=request.model_dump(mode="json"),
                                    runtime_config=runtime_config,
                                )
                            )
                            with pending_lock:
                                pending_approvals[task_key] = entry
                            current.finish_conversation_stream(
                                thread_id, status="pending_approval"
                            )
                            publish(
                                "approval",
                                {
                                    "thread_id": run_result.thread_id,
                                    "request": entry["request"],
                                },
                            )

                            decision_event.wait()

                            with pending_lock:
                                pending_approvals.pop(task_key, None)
                            current.finish_conversation_stream(
                                thread_id, status="running"
                            )

                            run_result = resume_workflow(
                                thread_id=run_result.thread_id,
                                context=context,
                                approved=bool(entry["approved"]),
                                feedback=str(entry.get("feedback", "")),
                                selected_option=(
                                    str(entry["selected_option"])
                                    if entry.get("selected_option") is not None
                                    else None
                                ),
                                progress_reporter=report,
                            )
                            current.complete_approval(task_key)

                        final_thread_id = run_result.thread_id
                        envelope = state_to_result(run_result.state)
                        assistant_message = user_message_for_state(
                            run_result.state
                        )
                        live_message = live_message_for_state(run_result.state)
                    current.finish_run(
                        thread_id,
                        status=str(envelope["status"]),
                        result=envelope,
                    )
                    publish_text(live_message)
                    publish("result", envelope)
                    visible_message = "".join(conversation_parts)
                    current.append_exchange(
                        task_key,
                        thread_id=thread_id,
                        user_message=body.message,
                        assistant_message=visible_message or assistant_message,
                    )
                    exchange_persisted = True
                    stream_terminal_status = "completed"
                    # 只学习用户显式选择且本次实际使用的稳定配置；不把整段
                    # 对话或模型推断自动升级为长期记忆。
                    if resolved_target_chip:
                        current.upsert_memory(
                            project_key=task_key,
                            memory_key="device.target_chip",
                            memory_type="device_config",
                            value={"target_chip": resolved_target_chip},
                            source_thread_id=thread_id,
                        )
                    if resolved_serial_port:
                        current.upsert_memory(
                            project_key=task_key,
                            memory_key="device.serial_port",
                            memory_type="device_config",
                            value={"serial_port": resolved_serial_port},
                            source_thread_id=thread_id,
                        )
                except (ValidationError, ValueError):
                    logger.exception(
                        "Workflow validation failed during %s phase for run %s",
                        phase,
                        thread_id,
                    )
                    if phase == "startup":
                        category = "startup"
                        message = "运行配置无效，请检查服务端环境变量"
                    elif phase == "workflow":
                        category = "validation"
                        message = "任务处理结果校验失败，请检查服务端日志"
                    else:
                        category = "internal"
                        message = "任务执行异常，请检查服务端日志"
                    failure_message = message
                    finish_failed_run(
                        current,
                        category=category,
                        message=message,
                    )
                    publish("error", {"category": category, "message": message})
                except Exception:
                    # Web 展示边界不返回异常原文、路径、源码或第三方响应。
                    logger.exception(
                        "Workflow failed during %s phase for run %s",
                        phase,
                        thread_id,
                    )
                    finish_failed_run(
                        current,
                        category="internal",
                        message="任务执行异常，请检查服务端日志",
                    )
                    failure_message = "任务执行异常，请检查服务端日志"
                    publish(
                        "error",
                        {
                            "category": "internal",
                            "message": "任务执行异常，请检查服务端日志",
                        },
                    )
                finally:
                    if not exchange_persisted:
                        try:
                            visible_failure = "".join(conversation_parts)
                            visible_failure += (
                                "\n\n**任务未完成：** " + failure_message
                            )
                            current.append_exchange(
                                task_key,
                                thread_id=thread_id,
                                user_message=body.message,
                                assistant_message=visible_failure,
                            )
                        except Exception:
                            logger.exception(
                                "Failed to persist failed conversation %s", thread_id
                            )
                    publish("done", "[DONE]")
                    try:
                        current.finish_conversation_stream(
                            thread_id,
                            status=stream_terminal_status,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to finish conversation stream %s", thread_id
                        )
                    if continuous_turn is not None:
                        try:
                            turn_message = "".join(conversation_parts)
                            if not turn_message and stream_terminal_status != "completed":
                                turn_message = failure_message
                            current.finish_agent_turn(
                                thread_id,
                                status=(
                                    "completed"
                                    if stream_terminal_status == "completed"
                                    else "failed"
                                ),
                                assistant_message=turn_message,
                                failure=(
                                    None
                                    if stream_terminal_status == "completed"
                                    else {
                                        "category": "internal",
                                        "message": failure_message,
                                    }
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "Failed to finish Agent Turn %s", thread_id
                            )
                    with active_lock:
                        active_projects.discard(task_key)
                        capacity.release()

            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            try:
                while True:
                    event, data = events.get()
                    yield _sse_event(event, data)
                    if event == "done":
                        break
            finally:
                # 当前只停止向已断开的浏览器写事件，不宣称取消后台构建。
                disconnected.set()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers=conversation_headers(thread_id),
        )

    @app.post("/api/conversations/{project}/approval")
    def decide_approval(
        project: str,
        body: WebApprovalDecision,
    ) -> dict[str, object]:
        resolve_project(project, body.root_index)
        key = _task_key(body.root_index, project)
        current: PersistencePort = app.state.persistence
        record = current.get_pending_approval(key)
        if record is None:
            raise HTTPException(
                status_code=409,
                detail="该项目没有待处理的工作流交互",
            )
        config = record.runtime_config
        recovery_family = str(config.get("workflow_family", ""))
        recovery_specialized = recovery_family in {
            "project_inspection",
            "knowledge_task",
        }
        recovery_supervisor = config.get("agent_runtime") == "supervisor"
        production_recovery_bootstrap = (
            production_specialized_bootstrap
            if recovery_specialized
            else (
                production_agent_bootstrap
                if recovery_supervisor
                else production_bootstrap
            )
        )
        with pending_lock:
            entry = pending_approvals.get(key)
        recovery_idf_command = None
        if (
            entry is None
            and production_recovery_bootstrap
            and not recovery_specialized
        ):
            recovery_idf_command = _require_idf_command()
        recovery_idf_path = (
            selected_toolchain_manager.status.idf_path
            if production_recovery_bootstrap
            and not recovery_supervisor
            and not recovery_specialized
            else None
        )

        approved = body.decision == "approve"
        if not current.decide_approval(key, approved):
            raise HTTPException(status_code=409, detail="工作流交互已被处理")

        if recovery_family == "continuous_agent":
            selected_checkpointer: BaseCheckpointSaver | None = (
                app.state.checkpointer
            )
            if selected_checkpointer is None:
                current.save_pending_approval(record)
                raise HTTPException(
                    status_code=409,
                    detail="持续 Agent checkpoint 不可用，无法恢复审批",
                )
            project_path = resolve_project(
                record.project_name,
                record.root_index,
            )
            resume_cancel_event = threading.Event()
            with continuous_active_lock:
                if record.thread_id in continuous_active_sessions:
                    current.save_pending_approval(record)
                    raise HTTPException(
                        status_code=409,
                        detail="该 Agent Session 正在恢复审批结果",
                    )
                continuous_active_sessions[record.thread_id] = {
                    "project_key": record.task_key,
                    "turn_id": str(config.get("turn_id", "")),
                    "cancel_event": resume_cancel_event,
                }

            def finish_approval_resume() -> None:
                with continuous_active_lock:
                    continuous_active_sessions.pop(record.thread_id, None)

            try:
                continuous_context = _continuous_agent_context(
                    project_path=project_path,
                    target_chip=(
                        str(config["target_chip"])
                        if config.get("target_chip") is not None
                        else None
                    ),
                    selected_serial_port=(
                        str(config["serial_port"])
                        if config.get("serial_port") is not None
                        else None
                    ),
                    allow_dependency_downloads=bool(
                        config.get("allow_dependency_downloads", False)
                    ),
                    persistence=current,
                    cancellation_requested=resume_cancel_event.is_set,
                )
                return resume_continuous_agent_http_approval(
                    record=record,
                    approved=approved,
                    feedback=body.feedback,
                    context=continuous_context,
                    persistence=current,
                    checkpointer=selected_checkpointer,
                    on_complete=finish_approval_resume,
                )
            except (CapabilityError, ValueError) as error:
                # 审批恢复失败时重新开放同一审批；Tool Ledger 会阻止已经成功的
                # 外部动作因用户重试而被重复执行。
                finish_approval_resume()
                current.save_pending_approval(record)
                raise HTTPException(status_code=503, detail=str(error)) from error
            except Exception as error:
                finish_approval_resume()
                current.save_pending_approval(record)
                raise HTTPException(
                    status_code=503,
                    detail="持续 Agent 审批恢复初始化失败",
                ) from error

        if entry is not None:
            # 同进程仍有 SSE worker：唤醒它，由原 worker 发布最终事件。
            entry["approved"] = approved
            entry["feedback"] = body.feedback
            entry["selected_option"] = body.selected_option
            decision_event = entry["event"]
            assert isinstance(decision_event, threading.Event)
            decision_event.set()
            return {"status": "ok", "project": project}

        # 服务重启后的恢复路径：SQLite 审批记录和 SqliteSaver checkpoint
        # 是唯一依据，不依赖重启前的线程或 Python 字典。
        if not current.durable or app.state.checkpointer is None:
            raise HTTPException(status_code=409, detail="待审批任务无法跨进程恢复")

        project_path = resolve_project(record.project_name, record.root_index)
        recovery_stream_id = str(config.get("turn_id", "")) or record.thread_id
        recoverable_stream = current.get_conversation_stream(recovery_stream_id)

        def persist_recovery_event(
            event: str,
            data: dict[str, object] | str,
        ) -> None:
            if recoverable_stream is None:
                return
            try:
                current.append_conversation_stream_event(
                    recovery_stream_id,
                    event=event,
                    data=data,
                )
            except Exception:
                logger.exception(
                    "Failed to persist recovered stream event %s for %s",
                    event,
                    recovery_stream_id,
                )

        try:
            if recoverable_stream is not None:
                current.finish_conversation_stream(
                    recovery_stream_id, status="running"
                )
                persist_recovery_event(
                    "token",
                    {"token": "\n\n**审批已处理，正在恢复原任务…**\n\n"},
                )
            if recovery_specialized:
                specialized_recovery_options: dict[str, object] = {
                    "project_path": project_path,
                    "target_chip": config.get("target_chip"),
                    "checkpointer": app.state.checkpointer,
                    "persistence": current,
                    "project_key": key,
                    "knowledge_service": app.state.knowledge_service,
                }
                if production_specialized_bootstrap:
                    specialized_recovery_options["settings"] = (
                        selected_model_store.load().conversation.resolved()
                    )
                    specialized_recovery_options["document_reader"] = (
                        _configured_pdf_reader()
                    )
                context = selected_specialized_bootstrap(
                    **specialized_recovery_options
                )
                specialized_result = selected_specialized_resumer(
                    thread_id=record.thread_id,
                    context=context,
                    approved=approved,
                    feedback=body.feedback,
                    selected_option=body.selected_option,
                )
                run_result = specialized_result
                envelope = specialized_state_to_result(
                    specialized_result.state
                )
                assistant_message = specialized_user_message(
                    specialized_result.state
                )
            elif recovery_supervisor:
                agent_recovery_options: dict[str, object] = {
                    "project_path": project_path,
                    "allow_dependency_downloads": bool(
                        config.get("allow_dependency_downloads", False)
                    ),
                    "serial_port": config.get("serial_port"),
                }
                if recovery_idf_command is not None:
                    agent_recovery_options["idf_command"] = recovery_idf_command
                if production_agent_bootstrap:
                    agent_recovery_options["settings"] = (
                        selected_model_store.load().conversation.resolved()
                    )
                context = agent_bootstrap_factory(**agent_recovery_options)
                run_result = agent_workflow_resumer(
                    thread_id=record.thread_id,
                    context=context,
                    checkpointer=app.state.checkpointer,
                    approved=approved,
                    feedback=body.feedback,
                    selected_option=body.selected_option,
                    persistence=current,
                    project_key=key,
                )
                envelope = agent_state_to_result(run_result.state)
                assistant_message = agent_user_message_for_state(
                    run_result.state
                )
            else:
                recovery_options: dict[str, object] = {
                    "project_path": project_path,
                    "allow_dependency_downloads": bool(
                        config.get("allow_dependency_downloads", False)
                    ),
                    "serial_port": config.get("serial_port"),
                    "target_chip": config.get("target_chip"),
                    "checkpointer": app.state.checkpointer,
                    "persistence": current,
                    "project_key": key,
                    "knowledge_service": app.state.knowledge_service,
                    "sdk_example_knowledge": app.state.sdk_example_knowledge,
                }
                if recovery_idf_command is not None:
                    recovery_options["idf_command"] = recovery_idf_command
                if recovery_idf_path is not None:
                    recovery_options["idf_path"] = Path(recovery_idf_path)
                if production_bootstrap:
                    recovery_options["settings"] = (
                        selected_model_store.load().conversation.resolved()
                    )
                    recovery_options["document_reader"] = (
                        _configured_pdf_reader()
                    )
                context = bootstrap_factory(**recovery_options)
                run_result = resume_workflow(
                    thread_id=record.thread_id,
                    context=context,
                    approved=approved,
                    feedback=body.feedback,
                    selected_option=body.selected_option,
                )
                envelope = state_to_result(run_result.state)
                assistant_message = user_message_for_state(run_result.state)
            if run_result.pending_approval is not None:
                next_request = run_result.pending_approval.model_dump(mode="json")
                current.save_pending_approval(PendingApprovalRecord(
                    task_key=key,
                    project_name=record.project_name,
                    root_index=record.root_index,
                    thread_id=record.thread_id,
                    request=next_request,
                    runtime_config=config,
                ))
                if recoverable_stream is not None:
                    current.finish_conversation_stream(
                        recovery_stream_id, status="pending_approval"
                    )
                    persist_recovery_event(
                        "approval",
                        {
                            "thread_id": record.thread_id,
                            "request": next_request,
                        },
                    )
                return {
                    "status": "pending_approval",
                    "project": project,
                    "recovered": True,
                    "thread_id": record.thread_id,
                    "request": next_request,
                }
            current.complete_approval(key)
            current.finish_run(
                recovery_stream_id,
                status=str(envelope["status"]),
                result=envelope,
            )
            persist_recovery_event(
                "token", {"token": "\n\n" + assistant_message}
            )
            if recoverable_stream is not None:
                completed_stream = current.get_conversation_stream(
                    recovery_stream_id
                )
                if completed_stream is not None:
                    assistant_message = completed_stream.assistant_content
            task_text = str(config.get("task_text", ""))
            current.append_exchange(
                key,
                thread_id=recovery_stream_id,
                user_message=task_text,
                assistant_message=assistant_message,
            )
            persist_recovery_event("result", envelope)
            persist_recovery_event("done", "[DONE]")
            if recoverable_stream is not None:
                current.finish_conversation_stream(
                    recovery_stream_id, status="completed"
                )
            if bool(config.get("continuous_agent_v2", False)):
                current.finish_agent_turn(
                    recovery_stream_id,
                    status="completed",
                    assistant_message=assistant_message,
                )
            return {
                "status": "ok",
                "project": project,
                "recovered": True,
                "result": envelope,
            }
        except (ValidationError, ValueError):
            current.complete_approval(key, failed=True)
            persist_recovery_event(
                "error",
                {
                    "category": "recovery",
                    "message": "恢复任务的运行配置无效",
                },
            )
            persist_recovery_event("done", "[DONE]")
            if recoverable_stream is not None:
                current.finish_conversation_stream(
                    record.thread_id, status="failed"
                )
            raise HTTPException(status_code=503, detail="恢复任务的运行配置无效")
        except Exception:
            current.complete_approval(key, failed=True)
            persist_recovery_event(
                "error",
                {"category": "recovery", "message": "恢复待审批任务失败"},
            )
            persist_recovery_event("done", "[DONE]")
            if recoverable_stream is not None:
                current.finish_conversation_stream(
                    record.thread_id, status="failed"
                )
            raise HTTPException(status_code=500, detail="恢复待审批任务失败")

    return app


def build_parser() -> argparse.ArgumentParser:
    # 保留 luxar-web 兼容入口;推荐使用统一的 `luxar web`。
    parser = argparse.ArgumentParser(
        prog="luxar-web",
        description="启动 LUXAR LangGraph 本地 Web UI(兼容入口,推荐 `luxar web`)",
    )
    parser.add_argument(
        "--projects-root",
        type=Path,
        action="append",
        required=True,
        help="项目根目录,可重复传入多个",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=arguments.positive_integer,
        default=8000,
    )
    parser.add_argument(
        "--serial-port",
        type=arguments.serial_port,
        help="服务端默认串口(页面选择会按任务覆盖)",
    )
    parser.add_argument(
        "--target",
        type=arguments.target_chip,
        help="未绑定芯片的旧项目所用服务端默认值",
    )
    parser.add_argument(
        "--max-concurrent-workflows",
        type=arguments.positive_integer,
        default=2,
    )
    return parser


def serve(
    *,
    projects_roots: Sequence[Path],
    host: str = "127.0.0.1",
    port: int = 8000,
    serial_port: str | None = None,
    target_chip: str | None = None,
    max_concurrent_workflows: int = 2,
) -> int:
    """Web 网关的服务边界:CLI(`luxar web`)与兼容入口(`luxar-web`)共用。"""

    try:
        app = create_app(
            projects_roots=projects_roots,
            max_concurrent_workflows=max_concurrent_workflows,
            serial_port=serial_port,
            target_chip=target_chip,
        )
    except (WebProjectError, ValueError):
        print("项目根目录无效", file=sys.stderr)
        return 2

    toolchain_status = app.state.toolchain_manager.status
    if toolchain_status.available:
        print(
            f"ESP-IDF 环境可用：{toolchain_status.version}",
            file=sys.stderr,
        )
    else:
        print(
            f"ESP-IDF 环境不可用：{toolchain_status.message}",
            file=sys.stderr,
        )

    uvicorn.run(app, host=host, port=port)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return serve(
        projects_roots=args.projects_root,
        host=args.host,
        port=args.port,
        serial_port=args.serial_port,
        target_chip=args.target,
        max_concurrent_workflows=args.max_concurrent_workflows,
    )


if __name__ == "__main__":
    raise SystemExit(main())
