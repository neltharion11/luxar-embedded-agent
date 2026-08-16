from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest
from fastapi.testclient import TestClient

from luxar.application.runner import WorkflowProgress, WorkflowRunResult
from luxar.application.state import WorkflowState
from luxar.domain.devices import ApprovalRequest
from luxar.domain.errors import WorkflowError
from luxar.web import create_app


def _run_result(state: WorkflowState) -> WorkflowRunResult:
    return WorkflowRunResult(state=state, thread_id="test-thread")


def make_project(root: Path, name: str = "blink") -> Path:
    project = root / name
    project.mkdir()
    (project / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n"
        f"project({name})\n",
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
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        ui_path=ui,
    )
    client = TestClient(app)

    assert client.get("/").text == "<h1>LUXAR UI</h1>"
    assert client.get("/api/health").json() == {
        "status": "ok",
        "service": "luxar-langgraph",
    }
    projects = client.get("/api/workspace/projects").json()
    assert projects == {
        "roots": [{"index": 0, "label": tmp_path.name}],
        "projects": [
            {"name": "blink", "platform": "espidf", "root_index": 0}
        ],
    }
    assert str(tmp_path) not in json.dumps(projects)


def test_default_app_serves_migrated_original_ui(tmp_path: Path) -> None:
    make_project(tmp_path)
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
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

    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        received["runner"] = kwargs
        reporter = kwargs["progress_reporter"]
        assert callable(reporter)
        reporter(WorkflowProgress("requirement", "需求分析完成", 0))
        return _run_result(
            WorkflowState(
                task_text="SECRET_TASK_MUST_NOT_SERIALIZE",
                status="completed",
                attempts=1,
                trace=["analyze_requirement", "completed"],
            )
        )

    app = create_app(
        projects_roots=[tmp_path],
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
        "serial_port": None,
        "target_chip": None,
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
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **kwargs: calls.append(kwargs),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
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
        projects_roots=[tmp_path],
        bootstrap_factory=fail_bootstrap,  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
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
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(
            WorkflowState(status="completed", attempts=1, trace=[])
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
        projects_roots=[tmp_path],
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


def make_approval_request() -> ApprovalRequest:
    return ApprovalRequest(
        project_name="blink",
        port="COM3",
        target_chip="esp32",
        summary="即将向串口设备烧录固件，请确认目标芯片与串口",
        step_description="flash_project",
        attempts=0,
    )


def _run_task_in_background(
    client: TestClient,
) -> tuple[threading.Thread, list[object]]:
    response_holder: list[object] = []

    def run_request() -> None:
        response_holder.append(
            client.post("/api/conversations/blink", json={"message": "flash"})
        )

    thread = threading.Thread(target=run_request)
    thread.start()
    return thread, response_holder


def _wait_for_pending_approval(app, project: str = "blink") -> None:
    # 待审批条目按 "<root_index>:<project>" 存储,默认根索引为 0。
    key = "0:" + project
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if key in app.state.pending_approvals:
            return
        time.sleep(0.05)
    raise AssertionError("approval never became pending")


def test_web_approval_flow_approves_and_resumes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    make_project(tmp_path)
    resume_calls: list[dict[str, object]] = []
    context = object()

    def fake_bootstrap(**kwargs: object) -> object:
        return context

    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        return WorkflowRunResult(
            state=WorkflowState(status="planned", trace=[]),
            thread_id="thread-1",
            pending_approval=make_approval_request(),
        )

    def fake_resume(**kwargs: object) -> WorkflowRunResult:
        resume_calls.append(kwargs)
        return _run_result(
            WorkflowState(
                status="completed",
                approval_status="approved",
                trace=[],
            )
        )

    monkeypatch.setattr("luxar.web.resume_workflow", fake_resume)
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=fake_bootstrap,  # type: ignore[arg-type]
        workflow_runner=fake_runner,
        ui_path=tmp_path / "missing-ui.html",
    )
    client = TestClient(app)
    thread, responses = _run_task_in_background(client)
    _wait_for_pending_approval(app)
    assert thread.is_alive()

    decision = client.post(
        "/api/conversations/blink/approval",
        json={"decision": "approve"},
    )
    thread.join(timeout=5)

    assert decision.status_code == 200
    assert decision.json() == {"status": "ok", "project": "blink"}
    events = parse_sse(responses[0].text)
    assert [event for event, _ in events] == [
        "approval",
        "result",
        "done",
    ]
    approval_event = events[0][1]
    assert isinstance(approval_event, dict)
    assert approval_event["thread_id"] == "thread-1"
    request = approval_event["request"]
    assert set(request) == {
        "project_name",
        "port",
        "target_chip",
        "summary",
        "step_description",
        "attempts",
    }
    assert request["port"] == "COM3"
    assert str(tmp_path) not in json.dumps(events)
    assert resume_calls[0]["thread_id"] == "thread-1"
    assert resume_calls[0]["approved"] is True
    assert resume_calls[0]["context"] is context
    assert events[1][1]["status"] == "completed"
    assert app.state.pending_approvals == {}


def test_web_approval_rejection_terminates_workflow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    make_project(tmp_path)

    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        return WorkflowRunResult(
            state=WorkflowState(status="planned", trace=[]),
            thread_id="thread-2",
            pending_approval=make_approval_request(),
        )

    def fake_resume(**kwargs: object) -> WorkflowRunResult:
        assert kwargs["approved"] is False
        error = WorkflowError(
            stage="flash",
            category="approval_rejected",
            message="烧录申请被用户拒绝",
            retryable=False,
            user_suggestion="确认目标芯片和串口后重新运行任务",
        )
        return _run_result(
            WorkflowState(status="failed", error=error, trace=[])
        )

    monkeypatch.setattr("luxar.web.resume_workflow", fake_resume)
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=fake_runner,
        ui_path=tmp_path / "missing-ui.html",
    )
    client = TestClient(app)
    thread, responses = _run_task_in_background(client)
    _wait_for_pending_approval(app)

    decision = client.post(
        "/api/conversations/blink/approval",
        json={"decision": "reject"},
    )
    thread.join(timeout=5)

    assert decision.status_code == 200
    events = parse_sse(responses[0].text)
    assert [event for event, _ in events] == [
        "approval",
        "result",
        "done",
    ]
    assert events[1][1]["error"]["category"] == "approval_rejected"


def test_web_approval_endpoint_rejects_without_pending_approval(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(
            WorkflowState(status="completed", trace=[])
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/conversations/blink/approval",
        json={"decision": "approve"},
    )

    assert response.status_code == 409


def test_web_approval_endpoint_rejects_invalid_decisions(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(
            WorkflowState(status="completed", trace=[])
        ),
    )
    client = TestClient(app)

    for payload in [{"decision": "maybe"}, {"decision": "yes"}, {}]:
        response = client.post(
            "/api/conversations/blink/approval",
            json=payload,
        )
        assert response.status_code == 422


def test_create_app_passes_server_side_serial_and_target_to_bootstrap(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    received: dict[str, object] = {}

    def fake_bootstrap(**kwargs: object) -> object:
        received.update(kwargs)
        return object()

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=fake_bootstrap,  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(
            WorkflowState(status="completed", trace=[])
        ),
        serial_port="COM4",
        target_chip="esp32s3",
    )
    client = TestClient(app)

    response = client.post(
        "/api/conversations/blink",
        json={"message": "flash"},
    )

    assert response.status_code == 200
    assert received["serial_port"] == "COM4"
    assert received["target_chip"] == "esp32s3"


def test_web_parser_rejects_invalid_serial_port_and_target(
    tmp_path: Path,
) -> None:
    from luxar.web import build_parser

    for args in [
        ["--projects-root", str(tmp_path), "--serial-port", "COM0"],
        ["--projects-root", str(tmp_path), "--serial-port", "COM4;rm"],
        ["--projects-root", str(tmp_path), "--target", "ESP32"],
    ]:
        with pytest.raises(SystemExit) as captured:
            build_parser().parse_args(args)
        assert captured.value.code == 2


def test_devices_ports_endpoint_lists_discovered_ports(
    tmp_path: Path,
) -> None:
    from luxar.domain.devices import SerialPortInfo

    make_project(tmp_path)
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        port_discoverer=lambda: [
            SerialPortInfo(
                name="COM4",
                description="USB-SERIAL CH340",
                hardware_id="USB VID:PID=1A86:7523",
            )
        ],
    )
    client = TestClient(app)

    response = client.get("/api/devices/ports")

    assert response.status_code == 200
    assert response.json() == {
        "ports": [
            {
                "name": "COM4",
                "description": "USB-SERIAL CH340",
                "hardware_id": "USB VID:PID=1A86:7523",
            }
        ]
    }


def test_task_selection_overrides_server_defaults_and_reaches_bootstrap(
    tmp_path: Path,
) -> None:
    from luxar.domain.devices import SerialPortInfo

    make_project(tmp_path)
    received: dict[str, object] = {}

    def fake_bootstrap(**kwargs: object) -> object:
        received.update(kwargs)
        return object()

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=fake_bootstrap,  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(
            WorkflowState(status="completed", trace=[])
        ),
        serial_port="COM9",
        target_chip="esp32",
        port_discoverer=lambda: [
            SerialPortInfo(name="COM4", description="CH340")
        ],
    )
    client = TestClient(app)

    response = client.post(
        "/api/conversations/blink",
        json={
            "message": "flash",
            "root_index": 0,
            "serial_port": "COM4",
            "target_chip": "esp32s3",
        },
    )

    assert response.status_code == 200
    assert received["serial_port"] == "COM4"
    assert received["target_chip"] == "esp32s3"


def test_task_rejects_serial_port_not_in_discovered_list(
    tmp_path: Path,
) -> None:
    from luxar.domain.devices import SerialPortInfo

    make_project(tmp_path)
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        port_discoverer=lambda: [
            SerialPortInfo(name="COM4", description="CH340")
        ],
    )
    client = TestClient(app)

    for port in ["COM5", "COM4;rm"]:
        response = client.post(
            "/api/conversations/blink",
            json={"message": "flash", "serial_port": port},
        )
        assert response.status_code == 422


def test_task_rejects_invalid_root_index(tmp_path: Path) -> None:
    make_project(tmp_path)
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
    )
    client = TestClient(app)

    response = client.post(
        "/api/conversations/blink",
        json={"message": "build", "root_index": 3},
    )

    assert response.status_code == 422


def test_multi_root_project_resolution(
    tmp_path: Path,
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    make_project(root_a, "shared")
    make_project(root_b, "shared")

    app = create_app(
        projects_roots=[root_a, root_b],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(
            WorkflowState(status="completed", trace=[])
        ),
    )
    client = TestClient(app)

    listing = client.get("/api/workspace/projects").json()
    assert listing["roots"] == [
        {"index": 0, "label": "a"},
        {"index": 1, "label": "b"},
    ]
    assert {p["root_index"] for p in listing["projects"]} == {0, 1}

    # 同名项目在不同根下互不干扰。
    for root_index in (0, 1):
        response = client.post(
            "/api/conversations/shared",
            json={"message": "build", "root_index": root_index},
        )
        assert response.status_code == 200
