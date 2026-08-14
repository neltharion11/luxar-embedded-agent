from __future__ import annotations

import json
from pathlib import Path
import threading

from fastapi.testclient import TestClient

from luxar.application.runner import WorkflowProgress
from luxar.application.state import WorkflowState
from luxar.web import create_app


def make_project(root: Path, name: str = "blink") -> Path:
    project = root / name
    project.mkdir()
    (project / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n",
        encoding="utf-8",
    )
    return project


def parse_sse(text: str) -> list[tuple[str, object]]:
    parsed: list[tuple[str, object]] = []
    for frame in text.strip().split("\n\n"):
        lines = frame.splitlines()
        event = lines[0].removeprefix("event: ")
        raw = lines[1].removeprefix("data: ")
        parsed.append((event, raw if raw == "[DONE]" else json.loads(raw)))
    return parsed


def test_app_serves_ui_health_and_safe_project_list(tmp_path: Path) -> None:
    make_project(tmp_path)
    ui = tmp_path / "index.html"
    ui.write_text("<h1>LUXAR UI</h1>", encoding="utf-8")
    app = create_app(
        projects_root=tmp_path,
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: WorkflowState(),
        ui_path=ui,
    )
    client = TestClient(app)

    assert client.get("/").text == "<h1>LUXAR UI</h1>"
    assert client.get("/api/health").json() == {
        "status": "ok",
        "service": "luxar-langgraph",
    }
    projects = client.get("/api/workspace/projects").json()
    assert projects == {"projects": [{"name": "blink", "platform": "espidf"}]}
    assert str(tmp_path) not in json.dumps(projects)


def test_default_app_serves_migrated_original_ui(tmp_path: Path) -> None:
    make_project(tmp_path)
    app = create_app(
        projects_root=tmp_path,
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: WorkflowState(),
    )

    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "Luxar" in response.text
    assert "currentEvent === 'progress'" in response.text
    assert "/api/health" in response.text


def test_sse_runs_shared_application_and_emits_allowlisted_result(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    received: dict[str, object] = {}
    context = object()

    def fake_bootstrap(**kwargs: object) -> object:
        received["bootstrap"] = kwargs
        return context

    def fake_runner(**kwargs: object) -> WorkflowState:
        received["runner"] = kwargs
        reporter = kwargs["progress_reporter"]
        assert callable(reporter)
        reporter(WorkflowProgress("requirement", "需求分析完成", 0))
        return WorkflowState(
            task_text="SECRET_TASK_MUST_NOT_SERIALIZE",
            status="completed",
            attempts=1,
            trace=["analyze_requirement", "completed"],
        )

    app = create_app(
        projects_root=tmp_path,
        bootstrap_factory=fake_bootstrap,  # type: ignore[arg-type]
        workflow_runner=fake_runner,
        ui_path=tmp_path / "missing-ui.html",
    )
    response = TestClient(app).post(
        "/api/conversations/blink",
        json={
            "message": "  build GPIO  ",
            "max_attempts": 5,
            "allow_dependency_downloads": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [event for event, _ in events] == ["progress", "result", "done"]
    assert events[0][1] == {
        "stage": "requirement",
        "message": "需求分析完成",
        "attempts": 0,
    }
    result = events[1][1]
    assert isinstance(result, dict)
    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert "task_text" not in result
    assert "SECRET_TASK_MUST_NOT_SERIALIZE" not in response.text
    assert str(tmp_path) not in response.text
    assert received["bootstrap"] == {
        "project_path": project.resolve(),
        "allow_dependency_downloads": True,
    }
    runner = received["runner"]
    assert isinstance(runner, dict)
    assert runner["context"] is context
    assert runner["initial_state"] == {
        "task_text": "build GPIO",
        "attempts": 0,
        "max_attempts": 5,
        "trace": [],
    }


def test_web_rejects_invalid_project_before_bootstrap(tmp_path: Path) -> None:
    make_project(tmp_path)
    calls: list[object] = []

    app = create_app(
        projects_root=tmp_path,
        bootstrap_factory=lambda **kwargs: calls.append(kwargs),  # type: ignore[arg-type]
        workflow_runner=lambda **_: WorkflowState(),
    )
    response = TestClient(app).post(
        "/api/conversations/..%5Csecret",
        json={"message": "build"},
    )

    assert response.status_code == 404
    assert calls == []
    assert str(tmp_path) not in response.text


def test_web_sanitizes_startup_error(tmp_path: Path) -> None:
    make_project(tmp_path)

    def fail_bootstrap(**_: object) -> object:
        raise ValueError("SECRET_API_KEY_DETAIL")

    app = create_app(
        projects_root=tmp_path,
        bootstrap_factory=fail_bootstrap,  # type: ignore[arg-type]
        workflow_runner=lambda **_: WorkflowState(),
    )
    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "build"},
    )

    events = parse_sse(response.text)
    assert [event for event, _ in events] == ["error", "done"]
    assert events[0][1] == {
        "category": "startup",
        "message": "运行配置无效，请检查服务端环境变量",
    }
    assert "SECRET_API_KEY_DETAIL" not in response.text


def test_process_local_history_and_reset(tmp_path: Path) -> None:
    make_project(tmp_path)
    app = create_app(
        projects_root=tmp_path,
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: WorkflowState(
            status="completed", attempts=1, trace=[]
        ),
    )
    client = TestClient(app)

    client.post("/api/conversations/blink", json={"message": "build"})
    history = client.get("/api/conversations/blink").json()
    assert history["durable"] is False
    assert history["messages"] == [
        {"role": "user", "content": "build"},
        {"role": "assistant", "content": "工作流状态：completed"},
    ]

    assert client.post("/api/conversations/blink/reset").status_code == 200
    assert client.get("/api/conversations/blink").json()["messages"] == []


def test_same_project_concurrent_request_is_rejected(tmp_path: Path) -> None:
    make_project(tmp_path)
    runner_started = threading.Event()
    release_runner = threading.Event()

    def blocking_runner(**_: object) -> WorkflowState:
        runner_started.set()
        assert release_runner.wait(timeout=5)
        return WorkflowState(status="completed", trace=[])

    app = create_app(
        projects_root=tmp_path,
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=blocking_runner,
    )
    first_response: list[object] = []

    def run_first_request() -> None:
        first_response.append(
            TestClient(app).post(
                "/api/conversations/blink",
                json={"message": "first"},
            )
        )

    first = threading.Thread(target=run_first_request)
    first.start()
    assert runner_started.wait(timeout=5)

    second = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "second"},
    )
    release_runner.set()
    first.join(timeout=5)

    assert second.status_code == 409
    assert second.json() == {"detail": "该项目已有正在运行的任务"}
    assert len(first_response) == 1
