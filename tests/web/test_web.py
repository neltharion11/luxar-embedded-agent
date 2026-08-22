from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from luxar.adapters.deepseek.conversation_router import (
    DeepSeekConversationRouter,
)
from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.application.runner import WorkflowProgress, WorkflowRunResult
from luxar.application.state import WorkflowState
from luxar.domain.devices import ApprovalRequest
from luxar.domain.conversation import ConversationDecision
from luxar.domain.errors import WorkflowError
from luxar.domain.evidence import BuildEvidence
from luxar.domain.requirements import FirmwareRequirement
from luxar.toolchain import EspIdfToolchainManager
from luxar.web import create_app
from luxar.database.persistence import (
    PendingApprovalRecord,
    TransientPersistence,
)


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

    index_response = client.get("/")
    assert index_response.text == "<h1>LUXAR UI</h1>"
    assert index_response.headers["cache-control"] == "no-store"
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


def test_toolchain_status_and_manual_selection_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IDF_PATH", raising=False)
    monkeypatch.delenv("IDF_PYTHON_ENV_PATH", raising=False)
    monkeypatch.setattr("luxar.toolchain.shutil.which", lambda _: None)
    idf_path = tmp_path / "sdk" / "esp-idf"
    (idf_path / "tools").mkdir(parents=True)
    (idf_path / "tools" / "idf.py").write_text(
        "# idf.py\n",
        encoding="utf-8",
    )
    manager = EspIdfToolchainManager(
        config_path=tmp_path / ".luxar" / "toolchain.json",
        probe=lambda command, root: (True, "ESP-IDF v6.0.2"),
        installer_config_paths=[],
        idf_search_paths=[],
    )
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            toolchain_manager=manager,
            toolchain_directory_picker=lambda: idf_path,
        )
    )

    missing = client.get("/api/toolchains/espidf")
    selected = client.post("/api/toolchains/espidf/select-directory")
    refreshed = client.post("/api/toolchains/espidf/refresh")

    assert missing.json()["available"] is False
    assert selected.status_code == 200
    assert selected.json() == {
        "available": True,
        "source": "configured",
        "version": "ESP-IDF v6.0.2",
        "idf_path": str(idf_path.resolve()),
        "message": "ESP-IDF 工具链可用",
    }
    assert refreshed.json()["available"] is True


def test_missing_toolchain_blocks_default_project_creation_and_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IDF_PATH", raising=False)
    monkeypatch.delenv("IDF_PYTHON_ENV_PATH", raising=False)
    monkeypatch.setattr("luxar.toolchain.shutil.which", lambda _: None)
    make_project(tmp_path)
    manager = EspIdfToolchainManager(
        config_path=tmp_path / ".luxar" / "toolchain.json",
        installer_config_paths=[],
        idf_search_paths=[],
    )
    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            toolchain_manager=manager,
            conversation_router=DeepSeekConversationRouter(
                FakeJsonCompletionClient(
                    [{"intent": "firmware_task", "response": ""}]
                ),
                "fast-model",
            ),
        )
    )

    create_response = client.post(
        "/api/workspace/projects",
        json={"name": "new-project", "target_chip": "esp32", "root_index": 0},
    )
    task_response = client.post(
        "/api/conversations/blink",
        json={"message": "build", "stream": True},
    )

    expected = {
        "detail": "未检测到可用的 ESP-IDF 环境，请先在仪表盘配置工具链"
    }
    assert create_response.status_code == 503
    assert create_response.json() == expected
    assert task_response.status_code == 503
    assert task_response.json() == expected


def test_greeting_uses_chat_branch_without_espidf_toolchain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IDF_PATH", raising=False)
    monkeypatch.delenv("IDF_PYTHON_ENV_PATH", raising=False)
    monkeypatch.setattr("luxar.toolchain.shutil.which", lambda _: None)
    make_project(tmp_path)
    manager = EspIdfToolchainManager(
        config_path=tmp_path / ".luxar" / "toolchain.json",
        installer_config_paths=[],
        idf_search_paths=[],
    )
    app = create_app(
        projects_roots=[tmp_path],
        toolchain_manager=manager,
        conversation_router=DeepSeekConversationRouter(
            FakeJsonCompletionClient(
                [{"intent": "casual_chat", "response": "你好，我是 LUXAR。"}]
            ),
            "fast-model",
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/api/conversations/blink",
        json={"message": "你好", "stream": True},
    )

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1] == ("done", "[DONE]")
    tokens = [
        data["token"]
        for event, data in events
        if event == "token" and isinstance(data, dict)
    ]
    assert len(tokens) >= 2
    assert "你好" in "".join(tokens)
    history = client.get("/api/conversations/blink").json()["messages"]
    assert history[0] == {"role": "user", "content": "你好"}
    assert history[1]["role"] == "assistant"


def test_injected_chat_router_skips_bootstrap_and_workflow(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    calls: list[str] = []

    class ChatRouter:
        def route(
            self,
            message: str,
            history: list[dict[str, str]],
            knowledge_status: str | None = None,
            previous_run: dict[str, object] | None = None,
        ) -> ConversationDecision:
            assert message == "介绍一下你自己"
            assert history == []
            assert knowledge_status is not None
            assert "未启用" in knowledge_status
            assert "LanceDB" in knowledge_status
            assert "PostgreSQL" not in knowledge_status
            assert previous_run is None
            return ConversationDecision(
                intent="casual_chat",
                response="我是 LUXAR。",
            )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: calls.append("bootstrap"),  # type: ignore[arg-type]
        workflow_runner=lambda **_: calls.append("workflow"),  # type: ignore[arg-type]
        conversation_router=ChatRouter(),
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "介绍一下你自己"},
    )

    assert parse_sse(response.text) == [
        ("token", {"token": "我是 LUXAR。"}),
        ("done", "[DONE]"),
    ]
    assert calls == []


def test_project_inspection_enters_shared_analysis_workflow(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    (project / "main").mkdir()
    (project / "main" / "main.c").write_text(
        "void app_main(void) {}\n",
        encoding="utf-8",
    )
    (project / "build").mkdir()
    calls: list[str] = []

    def inspection_runner(**kwargs: object) -> WorkflowRunResult:
        calls.append("workflow")
        initial = kwargs["initial_state"]
        assert isinstance(initial, dict)
        assert initial["task_mode"] == "inspection"
        return _run_result(
            WorkflowState(
                task_mode="inspection",
                status="completed",
                inspection_response=(
                    "项目 blink 的当前代码分析如下。\n\n"
                    "当前实现了最小 app_main 入口。\n\n"
                    "以上判断主要依据：main/main.c。"
                ),
                trace=["analyze_project", "report_project"],
            )
        )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: calls.append("bootstrap"),  # type: ignore[arg-type]
        workflow_runner=inspection_runner,
        conversation_router=DeepSeekConversationRouter(
            FakeJsonCompletionClient(
                [{"intent": "project_inspection", "response": ""}]
            ),
            "fast-model",
        ),
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "检查当前项目"},
    )

    text = "".join(
        data["token"]
        for event, data in parse_sse(response.text)
        if event == "token" and isinstance(data, dict)
    )
    assert response.status_code == 200
    assert "当前代码分析" in text
    assert "main/main.c" in text
    assert calls == ["bootstrap", "workflow"]


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
        reporter(
            WorkflowProgress(
                "requirement",
                "需求分析完成",
                0,
                narrative="需求目标：构建一个可编译的 GPIO 工程。\n\n",
            )
        )
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
    progress = next(data for event, data in events if event == "progress")
    assert progress == {
        "stage": "requirement",
        "message": "需求分析完成",
        "attempts": 0,
    }
    token_text = "".join(
        data["token"]
        for event, data in events
        if event == "token" and isinstance(data, dict)
    )
    assert "我先核对需求和当前项目代码" in token_text
    assert "需求目标：构建一个可编译的 GPIO 工程" in token_text
    assert "需求分析完成" not in token_text
    assert "✓" not in token_text
    assert "处理完成" in token_text
    result = next(data for event, data in events if event == "result")
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
        "task_mode": "firmware",
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


def test_sqlite_history_and_reset(tmp_path: Path) -> None:
    make_project(tmp_path)
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(
            WorkflowState(status="completed", attempts=1, trace=[])
        ),
    )
    with TestClient(app) as client:
        client.post("/api/conversations/blink", json={"message": "build"})
        history = client.get("/api/conversations/blink").json()
        assert history["durable"] is True
        assert history["messages"] == [
            {"role": "user", "content": "build"},
            {
                "role": "assistant",
                "content": (
                    "我先核对需求和当前项目代码，再决定是复用、"
                    "修改还是从零创建。\n\n处理完成。本次没有修改源码。"
                ),
            },
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
    deletion = TestClient(app).delete("/api/projects/blink")
    release_runner.set()
    first.join(timeout=5)

    assert second.status_code == 409
    assert second.json() == {"detail": "该项目已有正在运行的任务"}
    assert deletion.status_code == 409
    assert deletion.json() == {
        "detail": "项目有正在运行的任务，暂时不能删除"
    }
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
    assert events[-1] == ("done", "[DONE]")
    approval_event = next(data for event, data in events if event == "approval")
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
    token_text = "".join(
        data["token"]
        for event, data in events
        if event == "token" and isinstance(data, dict)
    )
    assert "我先核对需求和当前项目代码" in token_text
    assert "处理完成" in token_text
    result = next(data for event, data in events if event == "result")
    assert result["status"] == "completed"
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
    assert any(event == "approval" for event, _ in events)
    token_text = "".join(
        data["token"]
        for event, data in events
        if event == "token" and isinstance(data, dict)
    )
    assert "烧录申请被用户拒绝" in token_text
    result = next(data for event, data in events if event == "result")
    assert result["error"]["category"] == "approval_rejected"


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


def test_task_uses_immutable_target_from_project_configuration(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    (project / "sdkconfig.defaults").write_text(
        "CONFIG_IDF_TARGET=esp32c3\n",
        encoding="utf-8",
    )
    received: list[dict[str, object]] = []

    def fake_bootstrap(**kwargs: object) -> object:
        received.append(kwargs)
        return object()

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=fake_bootstrap,  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(
            WorkflowState(status="completed", trace=[])
        ),
        target_chip="esp32",
    )
    client = TestClient(app)

    accepted = client.post(
        "/api/conversations/blink",
        json={"message": "build"},
    )
    rejected = client.post(
        "/api/conversations/blink",
        json={"message": "build", "target_chip": "esp32s3"},
    )

    assert accepted.status_code == 200
    assert received[0]["target_chip"] == "esp32c3"
    assert rejected.status_code == 409
    assert rejected.json() == {
        "detail": "项目芯片已固定，不能在任务中更改"
    }
    assert len(received) == 1


def test_incomplete_requirement_streams_natural_clarification(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)

    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        reporter = kwargs["progress_reporter"]
        assert callable(reporter)
        reporter(WorkflowProgress("requirement", "需求分析完成", 0))
        reporter(WorkflowProgress("clarification", "需要补充需求信息", 0))
        return _run_result(
            WorkflowState(
                status="needs_clarification",
                requirement=FirmwareRequirement(
                    target="esp32",
                    feature="",
                    missing_fields=["feature"],
                ),
                trace=["analyze_requirement", "request_clarification"],
            )
        )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=fake_runner,
    )
    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "搭建一个项目"},
    )

    events = parse_sse(response.text)
    assert [
        data["stage"]
        for event, data in events
        if event == "progress" and isinstance(data, dict)
    ] == ["requirement", "clarification"]
    token_text = "".join(
        data["token"]
        for event, data in events
        if event == "token" and isinstance(data, dict)
    )
    assert "我先核对需求和当前项目代码" in token_text
    assert (
        "还需要你补充：项目需要实现的功能。"
        "如果只需要基础空项目，请直接回复“创建基础空项目”。"
    ) in token_text
    assert "needs_clarification" not in token_text


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


def test_project_memory_api_uses_repository(tmp_path: Path) -> None:
    make_project(tmp_path)
    persistence = TransientPersistence()
    app = create_app(projects_roots=[tmp_path], persistence=persistence)
    client = TestClient(app)

    saved = client.put(
        "/api/projects/blink/memories",
        json={
            "key": "device.target",
            "memory_type": "device_config",
            "value": {"target_chip": "esp32"},
            "confidence": 0.9,
        },
    )
    assert saved.status_code == 200
    response = client.get(
        "/api/projects/blink/memories?memory_type=device_config"
    )
    assert response.status_code == 200
    assert response.json()["memories"][0]["value"] == {
        "target_chip": "esp32"
    }


def test_database_health_reports_sqlite_mode(tmp_path: Path) -> None:
    make_project(tmp_path)
    with TestClient(create_app(projects_roots=[tmp_path])) as client:
        response = client.get("/api/health/database")
    payload = response.json()
    assert payload == {
        "status": "ok",
        "database": "sqlite",
        "durable": True,
        "application_path": str(
            (tmp_path.parent / ".luxar-data" / "luxar.sqlite3").resolve()
        ),
        "checkpoint_path": str(
            (tmp_path.parent / ".luxar-data" / "checkpoints.sqlite3").resolve()
        ),
        "knowledge_path": str(
            (tmp_path.parent / ".luxar-data" / "knowledge.lance").resolve()
        ),
        "sdk_knowledge_path": str(
            (tmp_path.parent / ".luxar-data" / "sdk-knowledge.lance").resolve()
        ),
    }


def test_followup_flash_question_uses_previous_run_without_new_workflow(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    workflow_calls = 0

    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        nonlocal workflow_calls
        workflow_calls += 1
        return WorkflowRunResult(
            state=WorkflowState(
                status="completed",
                attempts=1,
                changed_files=["main/t2.c"],
                build_evidence=BuildEvidence(
                    success=True,
                    command=["idf.py", "build"],
                    return_code=0,
                ),
                trace=["completed"],
            ),
            thread_id=str(kwargs["thread_id"]),
        )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=fake_runner,
        persistence=TransientPersistence(),
        conversation_router=DeepSeekConversationRouter(
            FakeJsonCompletionClient(
                [
                    {"intent": "firmware_task", "response": ""},
                    {
                        "intent": "workflow_status",
                        "response": (
                            "还没有烧录。上一轮构建已经通过，但没有执行烧录。"
                        ),
                    },
                ]
            ),
            "fast-model",
        ),
    )
    with TestClient(app) as client:
        first = client.post(
            "/api/conversations/blink",
            json={"message": "P13 输出低电平"},
        )
        second = client.post(
            "/api/conversations/blink",
            json={"message": "烧录了吗"},
        )
        history = client.get("/api/conversations/blink").json()

    assert first.status_code == 200
    assert second.status_code == 200
    assert workflow_calls == 1, (first.text, second.text)
    followup_text = "".join(
        data["token"]
        for event, data in parse_sse(second.text)
        if event == "token" and isinstance(data, dict)
    )
    assert "还没有烧录" in followup_text
    assert "构建已经通过" in followup_text
    assert history["messages"][-1]["content"] == followup_text


def test_restart_path_resumes_persisted_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_project(tmp_path)

    class DurableTestPersistence(TransientPersistence):
        durable = True

    persistence = DurableTestPersistence()
    persistence.start_run(
        thread_id="durable-thread",
        task_key="0:blink",
        project_name="blink",
        root_index=0,
        task_text="flash",
        runtime_config={},
    )
    request = make_approval_request()
    persistence.save_pending_approval(
        PendingApprovalRecord(
            task_key="0:blink",
            project_name="blink",
            root_index=0,
            thread_id="durable-thread",
            request=request.model_dump(mode="json"),
            runtime_config={
                "task_text": "flash",
                "serial_port": "COM3",
                "target_chip": "esp32",
            },
        )
    )
    context = object()
    bootstrap_calls: list[dict[str, object]] = []
    resume_calls: list[dict[str, object]] = []

    def fake_resume(**kwargs: object) -> WorkflowRunResult:
        resume_calls.append(kwargs)
        return WorkflowRunResult(
            state=WorkflowState(status="completed", trace=[]),
            thread_id="durable-thread",
        )

    monkeypatch.setattr("luxar.web.resume_workflow", fake_resume)
    app = create_app(
        projects_roots=[tmp_path],
        persistence=persistence,
        checkpointer=object(),  # type: ignore[arg-type]
        bootstrap_factory=lambda **kwargs: (
            bootstrap_calls.append(kwargs) or context
        ),  # type: ignore[arg-type]
    )
    response = TestClient(app).post(
        "/api/conversations/blink/approval",
        json={"decision": "approve"},
    )
    assert response.status_code == 200
    assert response.json()["recovered"] is True
    assert resume_calls[0]["thread_id"] == "durable-thread"
    assert resume_calls[0]["approved"] is True
    assert bootstrap_calls[0]["checkpointer"] is app.state.checkpointer
    assert persistence.get_messages("0:blink")[0]["content"] == "flash"


def test_knowledge_ingest_and_search_api(tmp_path: Path) -> None:
    make_project(tmp_path)

    class FakeKnowledge:
        def ingest(self, **kwargs: object):
            assert kwargs["project_key"] == "0:blink"
            return SimpleNamespace(
                document_id="doc-1",
                chunks=2,
                content_hash="abc",
            )

        def search(self, **kwargs: object):
            assert kwargs["query"] == "gpio"
            return [
                SimpleNamespace(
                    document_id="doc-1",
                    title="manual",
                    source_uri="manual://gpio",
                    ordinal=0,
                    content="GPIO guide",
                    score=0.9,
                )
            ]

    client = TestClient(
        create_app(
            projects_roots=[tmp_path],
            knowledge_service=FakeKnowledge(),  # type: ignore[arg-type]
        )
    )
    ingested = client.post(
        "/api/projects/blink/knowledge/documents",
        json={
            "source_uri": "manual://gpio",
            "title": "manual",
            "content": "GPIO guide",
        },
    )
    assert ingested.status_code == 200
    assert ingested.json()["chunks"] == 2
    searched = client.post(
        "/api/projects/blink/knowledge/search",
        json={"query": "gpio"},
    )
    assert searched.status_code == 200
    assert searched.json()["matches"][0]["source_uri"] == "manual://gpio"


def test_create_project_endpoint_uses_selected_root_and_catalog_validation(
    tmp_path: Path,
) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    calls: list[dict[str, object]] = []

    class FakeProjectCreator:
        def create_project(self, **kwargs: object) -> object:
            calls.append(kwargs)
            parent = kwargs["parent_dir"]
            name = kwargs["project_name"]
            assert isinstance(parent, Path)
            assert isinstance(name, str)
            project = parent / name
            project.mkdir()
            (project / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.16)\n"
                "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n"
                f"project({name})\n",
                encoding="utf-8",
            )
            return object()

    app = create_app(
        projects_roots=[root_a, root_b],
        project_creator=FakeProjectCreator(),  # type: ignore[arg-type]
    )
    client = TestClient(app)
    response = client.post(
        "/api/workspace/projects",
        json={
            "name": "new-blink",
            "target_chip": "esp32s3",
            "root_index": 1,
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "name": "new-blink",
        "platform": "espidf",
        "root_index": 1,
        "target_chip": "esp32s3",
    }
    assert calls == [
        {
            "parent_dir": root_b,
            "project_name": "new-blink",
            "target_chip": "esp32s3",
        }
    ]
    assert client.get("/api/workspace/projects").json()["projects"] == [
        {"name": "new-blink", "platform": "espidf", "root_index": 1}
    ]
    duplicate = client.post(
        "/api/workspace/projects",
        json={"name": "new-blink", "target_chip": "esp32", "root_index": 1},
    )
    assert duplicate.status_code == 409


def test_create_project_endpoint_rejects_unsafe_name_before_adapter(
    tmp_path: Path,
) -> None:
    creator_calls: list[object] = []
    app = create_app(
        projects_roots=[tmp_path],
        project_creator=lambda **kwargs: creator_calls.append(kwargs),  # type: ignore[arg-type]
    )
    response = TestClient(app).post(
        "/api/workspace/projects",
        json={"name": "../escape", "target_chip": "ESP32", "root_index": 0},
    )
    assert response.status_code == 422
    assert creator_calls == []


def test_delete_project_moves_entire_folder_to_recovery_area(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    (project / "main").mkdir()
    (project / "main" / "main.c").write_text(
        "void app_main(void) {}\n",
        encoding="utf-8",
    )
    persistence = TransientPersistence()
    persistence.append_exchange(
        "0:blink",
        thread_id="thread-1",
        user_message="build",
        assistant_message="done",
    )
    client = TestClient(
        create_app(projects_roots=[tmp_path], persistence=persistence)
    )

    response = client.delete("/api/projects/blink?root_index=0")

    assert response.status_code == 200
    assert response.json() == {
        "status": "deleted",
        "project": "blink",
        "recoverable": True,
    }
    assert not project.exists()
    recovered = list((tmp_path / ".luxar-trash").iterdir())
    assert len(recovered) == 1
    assert recovered[0].name.startswith("blink-")
    assert (recovered[0] / "main" / "main.c").is_file()
    assert persistence.get_messages("0:blink") == []
    assert client.get("/api/workspace/projects").json()["projects"] == []


def test_delete_project_rejects_pending_approval(tmp_path: Path) -> None:
    make_project(tmp_path)
    persistence = TransientPersistence()
    persistence.save_pending_approval(
        PendingApprovalRecord(
            task_key="0:blink",
            project_name="blink",
            root_index=0,
            thread_id="thread-approval",
            request={"summary": "flash"},
            runtime_config={},
        )
    )
    client = TestClient(
        create_app(projects_roots=[tmp_path], persistence=persistence)
    )

    response = client.delete("/api/projects/blink")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "项目有待处理的烧录审批，暂时不能删除"
    }
    assert (tmp_path / "blink").is_dir()




def test_select_project_directory_adds_its_parent_as_project_root(
    tmp_path: Path,
) -> None:
    configured_root = tmp_path / "configured"
    configured_root.mkdir()
    external_root = tmp_path / "external"
    external_root.mkdir()
    selected_project = make_project(external_root, "picked-project")
    app = create_app(
        projects_roots=[configured_root],
        project_directory_picker=lambda: selected_project,
    )
    client = TestClient(app)

    response = client.post(
        "/api/workspace/projects/select-directory",
        json={"target_chip": "esp32s3"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "project": {
            "name": "picked-project",
            "platform": "espidf",
            "root_index": 1,
            "target_chip": "esp32s3",
        }
    }
    assert client.get("/api/workspace/projects").json() == {
        "roots": [
            {"index": 0, "label": "configured"},
            {"index": 1, "label": "external"},
        ],
        "projects": [
            {
                "name": "picked-project",
                "platform": "espidf",
                "root_index": 1,
                "target_chip": "esp32s3",
            }
        ],
    }
    assert (selected_project / "sdkconfig.defaults").read_text(
        encoding="utf-8"
    ).endswith("CONFIG_IDF_TARGET=esp32s3\n")

    conflict = client.post(
        "/api/workspace/projects/select-directory",
        json={"target_chip": "esp32c3"},
    )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "项目芯片已固定，不能更改"}


def test_select_project_directory_handles_cancel_and_invalid_folder(
    tmp_path: Path,
) -> None:
    configured_root = tmp_path / "configured"
    configured_root.mkdir()
    plain_folder = tmp_path / "plain"
    plain_folder.mkdir()

    cancelled = TestClient(
        create_app(
            projects_roots=[configured_root],
            project_directory_picker=lambda: None,
        )
    ).post(
        "/api/workspace/projects/select-directory",
        json={"target_chip": "esp32"},
    )
    invalid = TestClient(
        create_app(
            projects_roots=[configured_root],
            project_directory_picker=lambda: plain_folder,
        )
    ).post(
        "/api/workspace/projects/select-directory",
        json={"target_chip": "esp32"},
    )

    assert cancelled.status_code == 200
    assert cancelled.json() == {"project": None}
    assert invalid.status_code == 422
    assert invalid.json() == {
        "detail": "所选文件夹不是有效的 ESP-IDF 项目"
    }
