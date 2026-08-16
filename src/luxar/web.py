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
from luxar.bootstrap import build_deepseek_runtime_context
from luxar.web_contracts import (
    WebApprovalDecision,
    WebHealth,
    WebProjectList,
    WebTaskRequest,
)
from luxar.web_projects import WebProjectCatalog, WebProjectError


BootstrapFactory = Callable[..., RuntimeContext]
WorkflowRunner = Callable[..., WorkflowRunResult]
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
    serial_port: str | None = None,
    target_chip: str | None = None,
) -> FastAPI:
    """创建可测试的 Web 应用；测试可注入 Fake Bootstrap 与 Runner。

    串口与芯片是服务端配置：浏览器只能提交任务文本，无法伪造端口。
    """

    if max_concurrent_workflows <= 0:
        raise ValueError("max_concurrent_workflows 必须是正整数")

    catalog = WebProjectCatalog(projects_root)
    selected_ui = ui_path or _default_ui_path()
    capacity = threading.BoundedSemaphore(max_concurrent_workflows)
    active_projects: set[str] = set()
    active_lock = threading.Lock()
    history: dict[str, list[dict[str, str]]] = {}
    history_lock = threading.Lock()
    # project -> 待处理审批的线程安全条目。
    pending_approvals: dict[str, dict[str, object]] = {}
    pending_lock = threading.Lock()

    app = FastAPI(title="LUXAR LangGraph API", version="0.1.0")
    # 测试通过 app.state 观察待处理审批。
    app.state.pending_approvals = pending_approvals  # type: ignore[attr-defined]

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
                        serial_port=serial_port,
                        target_chip=target_chip,
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
                            pending_approvals[project] = entry
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
                            pending_approvals.pop(project, None)

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

    @app.post("/api/conversations/{project}/approval")
    def decide_approval(
        project: str,
        body: WebApprovalDecision,
    ) -> dict[str, object]:
        resolve_project(project)
        with pending_lock:
            entry = pending_approvals.get(project)
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
    parser.add_argument("--projects-root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=arguments.positive_integer,
        default=8000,
    )
    parser.add_argument(
        "--serial-port",
        type=arguments.serial_port,
        help="开发板串口，例如 COM4（烧录/监控任务需要）",
    )
    parser.add_argument(
        "--target",
        type=arguments.target_chip,
        help="目标芯片，例如 esp32（创建任务建议提供）",
    )
    parser.add_argument(
        "--max-concurrent-workflows",
        type=arguments.positive_integer,
        default=2,
    )
    return parser


def serve(
    *,
    projects_root: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    serial_port: str | None = None,
    target_chip: str | None = None,
    max_concurrent_workflows: int = 2,
) -> int:
    """Web 网关的服务边界:CLI(`luxar web`)与兼容入口(`luxar-web`)共用。"""

    try:
        app = create_app(
            projects_root=projects_root,
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
        projects_root=args.projects_root,
        host=args.host,
        port=args.port,
        serial_port=args.serial_port,
        target_chip=args.target,
        max_concurrent_workflows=args.max_concurrent_workflows,
    )


if __name__ == "__main__":
    raise SystemExit(main())
