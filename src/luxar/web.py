"""LUXAR Web 展示入口：用安全 HTTP/SSE 合同调用现有 Bootstrap 与 Runner。"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
from collections.abc import Callable, Generator, Sequence
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError

from luxar.application.context import RuntimeContext
from luxar.application.results import state_to_result
from luxar.application.runner import WorkflowProgress, run_workflow
from luxar.application.state import WorkflowState
from luxar.bootstrap import build_deepseek_runtime_context
from luxar.web_contracts import WebHealth, WebProjectList, WebTaskRequest
from luxar.web_projects import WebProjectCatalog, WebProjectError


BootstrapFactory = Callable[..., RuntimeContext]
WorkflowRunner = Callable[..., WorkflowState]
_StreamItem = tuple[str, dict[str, object] | str]


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
    projects_root: Path,
    bootstrap_factory: BootstrapFactory = build_deepseek_runtime_context,
    workflow_runner: WorkflowRunner = run_workflow,
    ui_path: Path | None = None,
    max_concurrent_workflows: int = 2,
) -> FastAPI:
    """创建可测试的 Web 应用；测试可注入 Fake Bootstrap 与 Runner。"""

    if max_concurrent_workflows <= 0:
        raise ValueError("max_concurrent_workflows 必须是正整数")

    catalog = WebProjectCatalog(projects_root)
    selected_ui = ui_path or _default_ui_path()
    capacity = threading.BoundedSemaphore(max_concurrent_workflows)
    active_projects: set[str] = set()
    active_lock = threading.Lock()
    history: dict[str, list[dict[str, str]]] = {}
    history_lock = threading.Lock()

    app = FastAPI(title="LUXAR LangGraph API", version="0.1.0")

    def resolve_project(project: str) -> Path:
        try:
            return catalog.resolve(project)
        except WebProjectError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

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
        return WebProjectList(projects=catalog.list_projects())

    @app.get("/api/conversations/{project}")
    def get_conversation(project: str) -> dict[str, object]:
        resolve_project(project)
        with history_lock:
            messages = [dict(message) for message in history.get(project, [])]
        return {"messages": messages, "project": project, "durable": False}

    @app.post("/api/conversations/{project}/reset")
    def reset_conversation(project: str) -> dict[str, object]:
        resolve_project(project)
        with history_lock:
            history.pop(project, None)
        return {"status": "ok", "project": project, "durable": False}

    @app.post("/api/conversations/{project}")
    def run_task(
        project: str,
        body: WebTaskRequest,
    ) -> StreamingResponse:
        project_path = resolve_project(project)

        with active_lock:
            if project in active_projects:
                raise HTTPException(
                    status_code=409,
                    detail="该项目已有正在运行的任务",
                )
            if not capacity.acquire(blocking=False):
                raise HTTPException(
                    status_code=429,
                    detail="当前运行任务过多，请稍后重试",
                )
            active_projects.add(project)

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
                    )
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
                    envelope = state_to_result(run_result.state)
                    publish("result", envelope)
                    with history_lock:
                        messages = history.setdefault(project, [])
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
                        active_projects.discard(project)
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

    return app


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("必须是正整数") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="luxar-web",
        description="启动 LUXAR LangGraph 本地 Web UI",
    )
    parser.add_argument("--projects-root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=_positive_integer, default=8000)
    parser.add_argument(
        "--max-concurrent-workflows",
        type=_positive_integer,
        default=2,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        app = create_app(
            projects_root=args.projects_root,
            max_concurrent_workflows=args.max_concurrent_workflows,
        )
    except (WebProjectError, ValueError):
        print("项目根目录无效", file=sys.stderr)
        return 2

    uvicorn.run(app, host=args.host, port=args.port)
    return 0
