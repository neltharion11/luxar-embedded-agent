"""LUXAR Web 展示入口：用安全 HTTP/SSE 合同调用现有 Bootstrap 与 Runner。

项目根、串口与芯片都在页面选择后随任务提交：项目根必须在服务器
配置的根列表内，串口必须通过平台模式校验并出现在服务器实时发现
的列表里，芯片必须是小写标识符。任意值永远不会到达 idf.py。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
from collections.abc import Callable, Generator, Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError

from luxar import arguments
from luxar.application.context import RuntimeContext
from luxar.application.results import state_to_result
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
from luxar.domain.devices import SerialPortInfo
from luxar.web_contracts import (
    WebApprovalDecision,
    WebHealth,
    WebProject,
    WebProjectList,
    WebProjectRoot,
    WebSerialPort,
    WebSerialPortList,
    WebTaskRequest,
)
from luxar.web_projects import WebProjectCatalog, WebProjectError


BootstrapFactory = Callable[..., RuntimeContext]
WorkflowRunner = Callable[..., WorkflowRunResult]
PortDiscoverer = Callable[[], list[SerialPortInfo]]
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


def _default_ui_path() -> Path:
    return Path(__file__).with_name("ui") / "index.html"


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
) -> FastAPI:
    """创建可测试的 Web 应用；测试可注入 Fake Bootstrap 与 Runner。

    serial_port/target_chip 只是服务端默认值：页面选择会按任务覆盖。
    项目根列表在启动时配置，页面只能在允许的根之间切换。
    """

    if max_concurrent_workflows <= 0:
        raise ValueError("max_concurrent_workflows 必须是正整数")

    roots = tuple(projects_roots)
    if not roots:
        raise ValueError("projects_roots 至少需要一个项目根目录")

    catalogs = tuple(WebProjectCatalog(root) for root in roots)
    root_labels = [
        root.name or f"root-{index}"
        for index, root in enumerate(roots)
    ]
    selected_ui = ui_path or _default_ui_path()
    capacity = threading.BoundedSemaphore(max_concurrent_workflows)
    active_projects: set[str] = set()
    active_lock = threading.Lock()
    history: dict[str, list[dict[str, str]]] = {}
    history_lock = threading.Lock()
    # "<root_index>:<project>" -> 待处理审批的线程安全条目。
    pending_approvals: dict[str, dict[str, object]] = {}
    pending_lock = threading.Lock()

    app = FastAPI(title="LUXAR LangGraph API", version="0.1.0")
    # 测试通过 app.state 观察待处理审批。
    app.state.pending_approvals = pending_approvals  # type: ignore[attr-defined]

    def _root_index(raw: object) -> int:
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
        return FileResponse(selected_ui)

    @app.get("/api/health", response_model=WebHealth)
    def health() -> WebHealth:
        return WebHealth()

    @app.get("/api/workspace/projects", response_model=WebProjectList)
    def list_projects() -> WebProjectList:
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
                    )
                )
        projects.sort(
            key=lambda item: (item.root_index, item.name.casefold())
        )
        return WebProjectList(roots=root_items, projects=projects)

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
        with history_lock:
            messages = [dict(message) for message in history.get(key, [])]
        return {"messages": messages, "project": project, "durable": False}

    @app.post("/api/conversations/{project}/reset")
    def reset_conversation(
        project: str,
        root_index: int = 0,
    ) -> dict[str, object]:
        resolve_project(project, root_index)
        key = _task_key(root_index, project)
        with history_lock:
            history.pop(key, None)
        return {"status": "ok", "project": project, "durable": False}

    @app.post("/api/conversations/{project}")
    def run_task(
        project: str,
        body: WebTaskRequest,
    ) -> StreamingResponse:
        _root_index(body.root_index)
        _validate_task_port(body.serial_port)
        project_path = resolve_project(project, body.root_index)
        task_key = _task_key(body.root_index, project)
        # 页面选择优先,未选择时回退到服务端默认。
        resolved_serial_port = body.serial_port or serial_port
        resolved_target_chip = body.target_chip or target_chip

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

            def publish(event: str, data: dict[str, object] | str) -> None:
                while not disconnected.is_set():
                    try:
                        events.put((event, data), timeout=0.1)
                        return
                    except queue.Full:
                        continue

            def report(progress: WorkflowProgress) -> None:
                publish(
                    "progress",
                    {
                        "stage": progress.stage,
                        "message": progress.message,
                        "attempts": progress.attempts,
                    },
                )

            def worker() -> None:
                try:
                    context = bootstrap_factory(
                        project_path=project_path,
                        allow_dependency_downloads=(
                            body.allow_dependency_downloads
                        ),
                        serial_port=resolved_serial_port,
                        target_chip=resolved_target_chip,
                    )
                    # Web 不注入审批回调：烧录前工作流暂停并发布审批事件。
                    run_result = workflow_runner(
                        initial_state=WorkflowState(
                            task_text=body.message,
                            attempts=0,
                            max_attempts=body.max_attempts,
                            trace=[],
                        ),
                        context=context,
                        progress_reporter=report,
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

                    envelope = state_to_result(run_result.state)
                    publish("result", envelope)
                    with history_lock:
                        messages = history.setdefault(task_key, [])
                        messages.extend(
                            [
                                {"role": "user", "content": body.message},
                                {
                                    "role": "assistant",
                                    "content": (
                                        "工作流状态："
                                        f"{envelope['status']}"
                                    ),
                                },
                            ]
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
        with pending_lock:
            entry = pending_approvals.get(key)
            if entry is None:
                raise HTTPException(
                    status_code=409,
                    detail="该项目没有待处理的烧录审批",
                )

        # 决定写入共享条目后唤醒等待中的工作流线程。
        entry["approved"] = body.decision == "approve"
        decision_event = entry["event"]
        assert isinstance(decision_event, threading.Event)
        decision_event.set()
        return {"status": "ok", "project": project}

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
        help="服务端默认芯片(页面选择会按任务覆盖)",
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
