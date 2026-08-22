"""LUXAR Web 展示入口：用安全 HTTP/SSE 合同调用现有 Bootstrap 与 Runner。

项目根与芯片在创建或选择项目时固定；后续任务只允许选择串口。
项目根必须在服务器配置的根列表内，串口必须通过平台模式校验并
出现在服务器实时发现的列表里。任意值永远不会直接到达 idf.py。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import Callable, Generator, Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from langgraph.checkpoint.base import BaseCheckpointSaver

from luxar import arguments
from luxar.application.context import RuntimeContext
from luxar.application.results import (
    live_message_for_state,
    state_to_result,
    user_message_for_state,
)
from luxar.application.runner import (
    WorkflowProgress,
    WorkflowRunResult,
    resume_workflow,
    run_workflow,
)
from luxar.application.state import WorkflowState
from luxar.bootstrap import (
    build_deepseek_runtime_context,
    discover_serial_ports,
)
from luxar.adapters.deepseek.conversation_router import (
    DeepSeekConversationRouter,
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
from luxar.lance_knowledge import LanceDBKnowledgeIndex
from luxar.knowledge import (
    KnowledgeService,
    KnowledgeSettings,
    LocalHashEmbeddingAdapter,
    OpenAIEmbeddingAdapter,
)
from luxar.sdk_knowledge import SdkExampleKnowledgeBase
from luxar.ports.espidf_errors import EspIdfError
from luxar.ports.espidf_project import EspIdfProjectPort
from luxar.ports.conversation_router import ConversationRouter
from luxar.ports.errors import CapabilityError
from luxar.toolchain import EspIdfToolchainManager, EspIdfToolchainStatus
from luxar.web_contracts import (
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
    WebMemoryUpsert,
    WebKnowledgeIngest,
    WebKnowledgeSearch,
)
from luxar.web_projects import WebProjectCatalog, WebProjectError


BootstrapFactory = Callable[..., RuntimeContext]
WorkflowRunner = Callable[..., WorkflowRunResult]
PortDiscoverer = Callable[[], list[SerialPortInfo]]
ProjectDirectoryPicker = Callable[[], Path | None]
ProjectTrash = Callable[[Path, Path], None]
_StreamItem = tuple[str, dict[str, object] | str]

_SERIAL_PATTERN = re.compile(
    r"COM[1-9]\d*" if os.name == "nt" else r"/dev/tty(?:USB|ACM|S)\d+"
)


def _sse_event(event: str, data: dict[str, object] | str) -> str:
    """把安全 Python 数据编码成一个完整 SSE 帧。"""

    payload = (
        data
        if isinstance(data, str)
        else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    )
    return f"event: {event}\ndata: {payload}\n\n"


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
    selected_conversation_router = conversation_router
    if selected_conversation_router is None and production_bootstrap:
        # 正式入口的每条消息都交给模型选择处理模式。
        selected_conversation_router = DeepSeekConversationRouter()
    capacity = threading.BoundedSemaphore(max_concurrent_workflows)
    active_projects: set[str] = set()
    active_lock = threading.Lock()
    # 这里只保留同进程 SSE 等待器；审批事实由 PersistencePort 保存。
    pending_approvals: dict[str, dict[str, object]] = {}
    pending_lock = threading.Lock()

    storage: PersistencePort = persistence or TransientPersistence()
    local_runtime = storage_runtime
    if local_runtime is None and persistence is None:
        if production_bootstrap or "LUXAR_STORAGE_DIRECTORY" in os.environ:
            local_settings = LocalStorageSettings.for_projects_root(roots[0])
        else:
            local_settings = LocalStorageSettings(
                directory=roots[0] / ".luxar" / "storage"
            )
        local_runtime = LocalStorageRuntime(local_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        nonlocal storage, knowledge_service, sdk_example_knowledge
        if local_runtime is not None:
            local_runtime.open()
            storage = local_runtime.persistence
            application.state.persistence = storage
            application.state.checkpointer = local_runtime.checkpointer()
            embedding_settings = KnowledgeSettings()
            if knowledge_service is None and embedding_settings.configured:
                knowledge_index = LanceDBKnowledgeIndex(
                    local_runtime.settings.knowledge_path,
                    dimensions=embedding_settings.dimensions,
                )
                knowledge_service = KnowledgeService(
                    knowledge_index,
                    OpenAIEmbeddingAdapter(embedding_settings),
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
    app.state.checkpointer = checkpointer
    app.state.knowledge_service = knowledge_service
    app.state.sdk_example_knowledge = sdk_example_knowledge
    app.state.storage_runtime = local_runtime
    app.state.toolchain_manager = selected_toolchain_manager

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

    @app.get("/api/conversations/{project}")
    def get_conversation(
        project: str,
        root_index: int = 0,
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        key = _task_key(root_index, project)
        current: PersistencePort = app.state.persistence
        messages = current.get_messages(key)
        return {
            "messages": messages,
            "project": project,
            "durable": current.durable,
        }

    @app.post("/api/conversations/{project}/reset")
    def reset_conversation(
        project: str,
        root_index: int = 0,
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        key = _task_key(root_index, project)
        current: PersistencePort = app.state.persistence
        current.reset_conversation(key)
        return {
            "status": "ok",
            "project": project,
            "durable": current.durable,
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
                }
                for item in matches
            ]
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
        previous_run = current.get_latest_completed_run(task_key)
        knowledge_service: KnowledgeService | None = app.state.knowledge_service
        if knowledge_service is None:
            knowledge_status = (
                "项目外部知识库未启用（缺少 embedding 配置），"
                "当前没有可检索的项目 LanceDB 知识文档。ESP-IDF SDK 例程知识库"
                "是独立作用域，不能把其中内容当作当前项目资料。"
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
        try:
            decision = (
                selected_conversation_router.route(
                    body.message,
                    current.get_messages(task_key),
                    knowledge_status=knowledge_status,
                    previous_run=(
                        {
                            **previous_run.result,
                            "task_text": previous_run.task_text,
                            "status": previous_run.status,
                        }
                        if previous_run is not None
                        else None
                    ),
                )
                if selected_conversation_router is not None
                else None
            )
        except CapabilityError as error:
            raise HTTPException(
                status_code=503,
                detail="意图识别服务暂时不可用，请稍后重试",
            ) from error
        if decision is not None and decision.intent in {
            "casual_chat",
            "workflow_status",
        }:
            thread_id = uuid.uuid4().hex
            response_text = decision.response
            current.append_exchange(
                task_key,
                thread_id=thread_id,
                user_message=body.message,
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
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
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
        task_mode = (
            "inspection"
            if decision is not None and decision.intent == "project_inspection"
            else "firmware"
        )
        idf_command = (
            _require_idf_command()
            if production_bootstrap and task_mode == "firmware"
            else None
        )
        active_idf_path = (
            selected_toolchain_manager.status.idf_path
            if production_bootstrap and task_mode == "firmware"
            else None
        )
        # 页面选择优先,未选择时回退到服务端默认。
        resolved_serial_port = body.serial_port or serial_port
        resolved_target_chip = (
            project_target_chip or body.target_chip or target_chip
        )
        thread_id = uuid.uuid4().hex
        runtime_config: dict[str, object] = {
            "root_index": body.root_index,
            "project_name": project,
            "serial_port": resolved_serial_port,
            "target_chip": resolved_target_chip,
            "allow_dependency_downloads": body.allow_dependency_downloads,
            "task_text": body.message,
        }

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

        def event_stream() -> Generator[str, None, None]:
            events: queue.Queue[_StreamItem] = queue.Queue(maxsize=64)
            disconnected = threading.Event()
            conversation_parts: list[str] = []

            def publish(event: str, data: dict[str, object] | str) -> None:
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

            def report(progress: WorkflowProgress) -> None:
                publish(
                    "progress",
                    {
                        "stage": progress.stage,
                        "message": progress.message,
                        "attempts": progress.attempts,
                    },
                )
                # progress 驱动状态栏；narrative 驱动对话正文。项目检查模式
                # 自带一份完整检查报告，因此只让固件工作流逐步讲述过程。
                if task_mode == "firmware" and progress.narrative:
                    publish_text(progress.narrative)

            def worker() -> None:
                try:
                    current: PersistencePort = app.state.persistence
                    current.start_run(
                        thread_id=thread_id,
                        task_key=task_key,
                        project_name=project,
                        root_index=body.root_index,
                        task_text=body.message,
                        runtime_config=runtime_config,
                    )
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
                    if current.durable:
                        bootstrap_options.update(
                            {
                                "checkpointer": app.state.checkpointer,
                                "persistence": current,
                                "project_key": task_key,
                                "knowledge_service": app.state.knowledge_service,
                                "sdk_example_knowledge": (
                                    app.state.sdk_example_knowledge
                                ),
                            }
                        )
                    context = bootstrap_factory(
                        **bootstrap_options,
                    )
                    publish_text(
                        (
                            "我先读取并分析当前项目代码，再报告实际功能、结构和缺失项。\n\n"
                            if task_mode == "inspection"
                            else (
                                "我先核对需求和当前项目代码，再决定是复用、"
                                "修改还是从零创建。\n\n"
                            )
                        )
                    )
                    # Web 不注入审批回调：烧录前工作流暂停并发布审批事件。
                    run_result = workflow_runner(
                        initial_state=WorkflowState(
                            task_text=body.message,
                            task_mode=task_mode,
                            attempts=0,
                            max_attempts=body.max_attempts,
                            trace=[],
                        ),
                        context=context,
                        progress_reporter=report,
                        thread_id=thread_id,
                    )

                    if run_result.pending_approval is not None:
                        request = run_result.pending_approval
                        decision_event = threading.Event()
                        entry: dict[str, object] = {
                            "thread_id": run_result.thread_id,
                            "request": request.model_dump(mode="json"),
                            "event": decision_event,
                            "approved": False,
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
                        publish(
                            "approval",
                            {
                                "thread_id": run_result.thread_id,
                                "request": entry["request"],
                            },
                        )

                        decided = False
                        while not disconnected.is_set():
                            if decision_event.wait(timeout=0.2):
                                decided = True
                                break

                        with pending_lock:
                            pending_approvals.pop(task_key, None)

                        if not decided:
                            # 浏览器断开：工作流保持暂停，本次运行终止。
                            return

                        run_result = resume_workflow(
                            thread_id=run_result.thread_id,
                            context=context,
                            approved=bool(entry["approved"]),
                            progress_reporter=report,
                        )
                        current.complete_approval(task_key)

                    envelope = state_to_result(run_result.state)
                    assistant_message = user_message_for_state(
                        run_result.state
                    )
                    live_message = live_message_for_state(run_result.state)
                    current.finish_run(
                        run_result.thread_id,
                        status=str(envelope["status"]),
                        result=envelope,
                    )
                    publish_text(live_message)
                    publish("result", envelope)
                    visible_message = "".join(conversation_parts)
                    current.append_exchange(
                        task_key,
                        thread_id=run_result.thread_id,
                        user_message=body.message,
                        assistant_message=visible_message or assistant_message,
                    )
                    # 只学习用户显式选择且本次实际使用的稳定配置；不把整段
                    # 对话或模型推断自动升级为长期记忆。
                    if resolved_target_chip:
                        current.upsert_memory(
                            project_key=task_key,
                            memory_key="device.target_chip",
                            memory_type="device_config",
                            value={"target_chip": resolved_target_chip},
                            source_thread_id=run_result.thread_id,
                        )
                    if resolved_serial_port:
                        current.upsert_memory(
                            project_key=task_key,
                            memory_key="device.serial_port",
                            memory_type="device_config",
                            value={"serial_port": resolved_serial_port},
                            source_thread_id=run_result.thread_id,
                        )
                except (ValidationError, ValueError):
                    publish(
                        "error",
                        {
                            "category": "startup",
                            "message": "运行配置无效，请检查服务端环境变量",
                        },
                    )
                except Exception:
                    # Web 展示边界不返回异常原文、路径、源码或第三方响应。
                    publish(
                        "error",
                        {
                            "category": "internal",
                            "message": "任务执行异常，请检查服务端日志",
                        },
                    )
                finally:
                    publish("done", "[DONE]")
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
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
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
                detail="该项目没有待处理的烧录审批",
            )
        with pending_lock:
            entry = pending_approvals.get(key)
        recovery_idf_command = None
        if entry is None and production_bootstrap:
            recovery_idf_command = _require_idf_command()
        recovery_idf_path = (
            selected_toolchain_manager.status.idf_path
            if production_bootstrap
            else None
        )

        approved = body.decision == "approve"
        if not current.decide_approval(key, approved):
            raise HTTPException(status_code=409, detail="烧录审批已被处理")

        if entry is not None:
            # 同进程仍有 SSE worker：唤醒它，由原 worker 发布最终事件。
            entry["approved"] = approved
            decision_event = entry["event"]
            assert isinstance(decision_event, threading.Event)
            decision_event.set()
            return {"status": "ok", "project": project}

        # 服务重启后的恢复路径：SQLite 审批记录和 SqliteSaver checkpoint
        # 是唯一依据，不依赖重启前的线程或 Python 字典。
        if not current.durable or app.state.checkpointer is None:
            raise HTTPException(status_code=409, detail="待审批任务无法跨进程恢复")

        config = record.runtime_config
        project_path = resolve_project(record.project_name, record.root_index)
        try:
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
            context = bootstrap_factory(
                **recovery_options,
            )
            run_result = resume_workflow(
                thread_id=record.thread_id,
                context=context,
                approved=approved,
            )
            envelope = state_to_result(run_result.state)
            assistant_message = user_message_for_state(run_result.state)
            current.complete_approval(key)
            current.finish_run(
                record.thread_id,
                status=str(envelope["status"]),
                result=envelope,
            )
            task_text = str(config.get("task_text", ""))
            current.append_exchange(
                key,
                thread_id=record.thread_id,
                user_message=task_text,
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
            raise HTTPException(status_code=503, detail="恢复任务的运行配置无效")
        except Exception:
            current.complete_approval(key, failed=True)
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
