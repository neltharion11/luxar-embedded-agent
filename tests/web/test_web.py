from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from luxar.adapters.deepseek.conversation_router import (
    DeepSeekConversationRouter,
)
from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.settings import DeepSeekSettings
from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.application.agent_runner import (
    AgentWorkflowProgress,
    AgentWorkflowRunResult,
)
from luxar.application.runner import WorkflowProgress, WorkflowRunResult
from luxar.application.specialized_runner import (
    PdfSpecializedWorkflowProgress,
    SpecializedWorkflowRunResult,
)
from luxar.application.specialized_state import SpecializedWorkflowState
from luxar.application.state import WorkflowState
from luxar.bootstrap import build_deepseek_agent_runtime_context
from luxar.domain.devices import ApprovalRequest
from luxar.domain.agent.approvals import AgentApprovalRequest
from luxar.domain.agent.changes import CapabilityChange, ChangeSet
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.conversation import ConversationDecision
from luxar.domain.errors import WorkflowError
from luxar.domain.evidence import BuildDiagnostic, BuildEvidence
from luxar.domain.interactions import WorkflowInteraction
from luxar.domain.requirements import FirmwareRequirement
from luxar.toolchain import EspIdfToolchainManager
from luxar.web import create_app
from luxar.model_config import ModelConfigStore
from luxar.database.persistence import (
    PendingApprovalRecord,
    TransientPersistence,
)


def _run_result(state: WorkflowState) -> WorkflowRunResult:
    return WorkflowRunResult(state=state, thread_id="test-thread")


def _agent_run_result(state: dict[str, object]) -> AgentWorkflowRunResult:
    return AgentWorkflowRunResult(state=state, thread_id="agent-test-thread")


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


def test_conversation_snapshot_exposes_recoverable_active_run(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    persistence = TransientPersistence()
    persistence.start_conversation_stream(
        thread_id="stream-active",
        task_key="0:blink",
        user_message="设置 P32 为高电平并烧录",
    )
    persistence.append_conversation_stream_event(
        "stream-active",
        event="token",
        data={"token": "已检查工程，准备构建。"},
    )
    persistence.append_conversation_stream_event(
        "stream-active",
        event="progress",
        data={
            "progress_type": "pdf",
            "current": 18,
            "total": 36,
            "unit": "pages",
            "phase": "extracting",
            "batch": 2,
            "message": "已读取 18/36 页",
        },
    )
    persistence.save_pending_approval(
        PendingApprovalRecord(
            task_key="0:blink",
            project_name="blink",
            root_index=0,
            thread_id="stream-active",
            request={"title": "烧录审批", "summary": "即将写入设备"},
            runtime_config={},
        )
    )
    client = TestClient(
        create_app(projects_roots=[tmp_path], persistence=persistence)
    )

    response = client.get("/api/conversations/blink")

    assert response.status_code == 200
    active = response.json()["active_run"]
    assert active["thread_id"] == "stream-active"
    assert active["user_message"] == "设置 P32 为高电平并烧录"
    assert active["assistant_content"] == "已检查工程，准备构建。"
    assert active["last_sequence"] == 2
    assert active["progress"]["current"] == 18
    assert active["progress"]["total"] == 36
    assert active["pending_approval"]["title"] == "烧录审批"


def test_conversation_snapshot_preserves_failed_stream_without_history(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    persistence = TransientPersistence()
    persistence.start_conversation_stream(
        thread_id="stream-failed",
        task_key="0:blink",
        user_message="检索 OLED 资料并编写驱动",
    )
    persistence.append_conversation_stream_event(
        "stream-failed",
        event="commentary",
        data={"commentary_id": "c1", "token": "已完成资料检索。"},
    )
    persistence.append_conversation_stream_event(
        "stream-failed",
        event="error",
        data={"category": "recovery", "message": "审批恢复失败"},
    )
    persistence.append_conversation_stream_event(
        "stream-failed", event="done", data="[DONE]"
    )
    persistence.finish_conversation_stream("stream-failed", status="failed")

    client = TestClient(
        create_app(projects_roots=[tmp_path], persistence=persistence)
    )

    response = client.get("/api/conversations/blink")

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_run"] is None
    assert payload["messages"] == [
        {"role": "user", "content": "检索 OLED 资料并编写驱动"},
        {"role": "assistant", "content": "已完成资料检索。"},
    ]


def test_conversation_stream_replay_uses_sse_event_ids(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    persistence = TransientPersistence()
    persistence.start_conversation_stream(
        thread_id="stream-replay",
        task_key="0:blink",
        user_message="继续",
    )
    persistence.append_conversation_stream_event(
        "stream-replay", event="token", data={"token": "第一步。"}
    )
    persistence.append_conversation_stream_event(
        "stream-replay",
        event="tool_call",
        data={"tool_call": "espidf.build"},
    )
    persistence.append_conversation_stream_event(
        "stream-replay", event="done", data="[DONE]"
    )
    persistence.finish_conversation_stream("stream-replay", status="completed")
    client = TestClient(
        create_app(projects_roots=[tmp_path], persistence=persistence)
    )

    response = client.get(
        "/api/conversations/blink/streams/stream-replay?after_sequence=1"
    )

    assert response.status_code == 200
    assert "id: 2\nevent: tool_call" in response.text
    assert "id: 3\nevent: done\ndata: [DONE]" in response.text
    assert "第一步" not in response.text


def test_active_conversation_cannot_be_reset(tmp_path: Path) -> None:
    make_project(tmp_path)
    persistence = TransientPersistence()
    persistence.start_conversation_stream(
        thread_id="stream-active",
        task_key="0:blink",
        user_message="构建中",
    )
    client = TestClient(
        create_app(projects_roots=[tmp_path], persistence=persistence)
    )

    response = client.post("/api/conversations/blink/reset")

    assert response.status_code == 409
    assert persistence.get_conversation_stream("stream-active") is not None


def test_dashboard_model_config_supports_openai_local_and_secret_masking(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    store = ModelConfigStore(tmp_path / "settings" / "models.json")
    client = TestClient(create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        model_config_store=store,
    ))

    saved = client.put("/api/config/models", json={
        "conversation": {
            "provider": "openai",
            "api_key": "openai-secret",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-test",
            "timeout_seconds": 60.0,
            "thinking_enabled": True,
            "thinking_effort": "low",
            "context_window_tokens": 262144,
        },
        "vision_mode": "separate",
        "vision": {
            "provider": "local",
            "api_key": "local-vision-secret",
            "base_url": "http://127.0.0.1:9001/v1",
            "model": "qwen-vl",
            "timeout_seconds": 90.0,
        },
        "embedding": {
            "mode": "api",
            "provider": "local",
            "api_key": "local-embedding-secret",
            "base_url": "http://127.0.0.1:9002/v1",
            "model": "embedding-test",
            "dimensions": 64,
            "timeout_seconds": 30.0,
        },
    })

    assert saved.status_code == 200
    response_text = saved.text
    assert "openai-secret" not in response_text
    assert "local-vision-secret" not in response_text
    assert "local-embedding-secret" not in response_text
    assert saved.json()["conversation"]["provider"] == "openai"
    assert saved.json()["vision"]["provider"] == "local"
    assert saved.json()["conversation"]["api_key_configured"] is True
    assert saved.json()["conversation"]["context_window_tokens"] == 262144
    assert saved.json()["conversation"]["thinking_enabled"] is True
    assert saved.json()["conversation"]["thinking_effort"] == "low"
    assert saved.json()["conversation"]["context_compaction_threshold"] == 0.95
    assert saved.json()["embedding"] == {
        "mode": "api",
        "provider": "local",
        "base_url": "http://127.0.0.1:9002/v1",
        "model": "embedding-test",
        "dimensions": 64,
        "timeout_seconds": 30.0,
        "api_key_configured": True,
        "configured": True,
    }
    assert client.get("/api/config/models").json() == saved.json()


def test_local_hash_embedding_enables_project_knowledge_without_api_key(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        model_config_store=ModelConfigStore(tmp_path / "models.json"),
    )

    with TestClient(app) as client:
        config = client.get("/api/config/models").json()
        documents = client.get(
            "/api/projects/blink/knowledge/documents?root_index=0"
        )

    assert config["embedding"]["mode"] == "local_hash"
    assert config["embedding"]["configured"] is True
    assert documents.status_code == 200
    assert documents.json() == {"documents": []}


def test_dashboard_accepts_local_chat_without_api_key(tmp_path: Path) -> None:
    make_project(tmp_path)
    client = TestClient(create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        model_config_store=ModelConfigStore(tmp_path / "models.json"),
    ))

    saved = client.put("/api/config/models", json={
        "conversation": {
            "provider": "local",
            "base_url": "http://127.0.0.1:1234/v1",
            "model": "local-chat",
            "timeout_seconds": 60.0,
        },
        "vision_mode": "python",
    })

    assert saved.status_code == 200
    assert saved.json()["conversation"]["api_key_configured"] is False


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
    runtime = client.get("/api/runtime").json()
    assert runtime["mode"] == "legacy"
    assert runtime["reason"] == "unqualified_fallback"
    assert runtime["legacy_deprecated"] is True
    assert runtime["legacy_retirement_ready"] is False
    assert "specialized_workflows_extracted" not in (
        runtime["legacy_retirement_blocking_gates"]
    )
    assert "no_legacy_recovery_dependencies" in (
        runtime["legacy_retirement_blocking_gates"]
    )
    audit = client.get("/api/runtime/audit").json()
    assert audit["durable"] is False
    assert audit["qualifies_as_release_evidence"] is False
    assert "storage_not_durable" in audit["blocking_reasons"]
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


def test_flash_command_without_serial_asks_only_for_serial_port(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    # This test documents the explicit emergency fallback contract.
    app = create_app(
        projects_roots=[tmp_path],
        continuous_agent_enabled=False,
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "烧录"},
    )

    assert response.status_code == 200
    token_text = "".join(
        data["token"]
        for event, data in parse_sse(response.text)
        if event == "token" and isinstance(data, dict)
    )
    assert "还缺少开发板串口" in token_text
    assert "COM3" in token_text


def test_focused_knowledge_followup_returns_only_the_direct_answer(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    calls: list[str] = []

    def should_not_bootstrap(**_: object) -> object:
        calls.append("bootstrap")
        raise AssertionError("focused follow-up must not start a workflow")

    def should_not_run(**_: object) -> SpecializedWorkflowRunResult:
        calls.append("workflow")
        raise AssertionError("focused follow-up must not start a workflow")

    app = create_app(
        projects_roots=[tmp_path],
        specialized_bootstrap_factory=should_not_bootstrap,  # type: ignore[arg-type]
        specialized_workflow_runner=should_not_run,
        conversation_router=DeepSeekConversationRouter(
            FakeJsonCompletionClient(
                [
                    {
                        "intent": "knowledge_task",
                        "response": "SDA 是数据线，SCL 是时钟线。",
                        "response_plan": {
                            "operation": "direct_answer",
                            "context_required": True,
                            "scope": "focused",
                            "confidence": 0.95,
                            "ambiguity": 0.01,
                            "answer_budget": 240,
                        },
                    }
                ]
            ),
            "fast-model",
        ),
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "SCL 和 SDA 是哪两个引脚？"},
    )

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1] == ("done", "[DONE]")
    assert all(event != "progress" for event, _ in events)
    assert "".join(
        data["token"]
        for event, data in events
        if event == "token" and isinstance(data, dict)
    ) == "SDA 是数据线，SCL 是时钟线。"
    assert calls == []


def test_web_persists_rolling_summary_when_history_reaches_95_percent(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    persistence = TransientPersistence()
    for index in range(4):
        persistence.append_exchange(
            "0:blink",
            thread_id=f"history-{index}",
            user_message=f"需求 {index}：" + "GPIO 约束" * 350,
            assistant_message=f"结果 {index}：" + "工具证据" * 350,
        )
    client = FakeJsonCompletionClient(
        [
            {"summary": "用户持续修改 GPIO；必须保留已有能力和工具证据。"},
            {"intent": "casual_chat", "response": "上下文仍然可用。"},
        ]
    )
    router = DeepSeekConversationRouter(
        client,
        "custom-small-model",
        context_window_tokens=4096,
    )
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        conversation_router=router,
        persistence=persistence,
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "还记得之前的约束吗？"},
    )

    assert response.status_code == 200
    streamed = "".join(
        item["token"]
        for event, item in parse_sse(response.text)
        if event == "token" and isinstance(item, dict)
    )
    assert "对话上下文已达到模型窗口的 95%" in streamed
    memories = persistence.find_memories(
        "0:blink",
        memory_type="conversation_context",
    )
    assert len(memories) == 1
    assert memories[0].value["summary"].startswith("用户持续修改 GPIO")
    assert memories[0].value["covered_message_count"] > 0
    routed_payload = json.loads(client.calls[1][1])
    assert routed_payload["history"][0]["content"].startswith(
        "【LUXAR 压缩的早期对话上下文】"
    )
    reset = TestClient(app).post("/api/conversations/blink/reset")
    assert reset.status_code == 200
    cleared = persistence.find_memories(
        "0:blink",
        memory_type="conversation_context",
    )[0]
    assert cleared.value == {"summary": "", "covered_message_count": 0}


def test_web_retry_restores_latest_blocked_goal_and_failure_context(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    persistence = TransientPersistence()
    objective = ProjectObjective(
        objective_id="retry-p32",
        title="设置 P32 高电平并烧录",
        description="将 P32 配置为高电平，构建并烧录开发板",
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="modify",
                capability_id="gpio.output:P32",
                desired_state={"pin": 32, "level": 1},
            )
        ]
    )
    previous_build = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=2,
        stderr_summary="driver/gpio.h: No such file or directory",
        error_category="source",
        diagnostics=[
            BuildDiagnostic(
                file="components/ssd1306/ssd1306.h",
                line=6,
                column=10,
                severity="error",
                message="driver/gpio.h: No such file or directory",
            )
        ],
    )
    persistence.save_agent_project(
        project_key="0:blink",
        objective=objective.model_dump(mode="json"),
        change_set=change_set.model_dump(mode="json"),
        revision=1,
        capabilities=[],
        snapshot={
            "status": "blocked",
            "last_error": "ESP-IDF 构建未通过: source",
            "build_evidence": previous_build.model_dump(mode="json"),
        },
    )
    persistence.start_run(
        thread_id="blocked-p32",
        task_key="0:blink",
        project_name="blink",
        root_index=0,
        task_text="设置 P32 为高电平并烧录",
        runtime_config={},
    )
    persistence.finish_run(
        "blocked-p32",
        status="blocked",
        result={
            "status": "blocked",
            "last_error": "ESP-IDF 构建未通过: source",
            "changed_files": ["main/12345.c", "main/CMakeLists.txt"],
        },
    )
    captured: dict[str, object] = {}

    class MustNotRouteRetry:
        def route(self, *_: object, **__: object) -> ConversationDecision:
            raise AssertionError("明确的重试指令不应再次交给模型猜测")

    def agent_runner(**kwargs: object) -> AgentWorkflowRunResult:
        captured.update(kwargs)
        initial = kwargs["initial_state"]
        assert isinstance(initial, dict)
        return _agent_run_result(
            {
                **initial,
                "status": "completed",
                "acceptance_passed": True,
                "build_verified": True,
                "trace": [],
            }
        )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        agent_bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        agent_workflow_runner=agent_runner,
        agent_runtime_mode="supervisor",
        conversation_router=MustNotRouteRetry(),
        persistence=persistence,
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "重试"},
    )

    events = parse_sse(response.text)
    token_text = "".join(
        item["token"]
        for event, item in events
        if event == "token" and isinstance(item, dict)
    )
    initial = captured["initial_state"]
    assert isinstance(initial, dict)
    assert initial["objective"] == objective
    assert initial["build_evidence"] == previous_build
    assert "设置 P32 为高电平并烧录" in initial["task_text"]
    assert "ESP-IDF 构建未通过: source" in initial["task_text"]
    assert "已识别“重试”为承接上一任务的指令" in token_text
    assert "重新检查后从失败处修复" in token_text
    history = persistence.get_messages("0:blink")
    assert history[-2] == {"role": "user", "content": "重试"}


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


def test_project_inspection_prefers_dedicated_workflow_and_records_family(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    calls: list[str] = []

    class RecordingPersistence(TransientPersistence):
        def __init__(self) -> None:
            super().__init__()
            self.runtime_configs: list[dict[str, object]] = []

        def start_run(self, **values: object) -> None:
            self.runtime_configs.append(
                dict(values["runtime_config"])  # type: ignore[arg-type]
            )
            super().start_run(**values)

    persistence = RecordingPersistence()

    def legacy_bootstrap(**_: object) -> object:
        raise AssertionError("inspection must not bootstrap legacy firmware")

    def legacy_runner(**_: object) -> WorkflowRunResult:
        raise AssertionError("inspection must not enter legacy firmware graph")

    def specialized_bootstrap(**_: object) -> object:
        calls.append("specialized_bootstrap")
        return object()

    def specialized_runner(**kwargs: object) -> SpecializedWorkflowRunResult:
        initial = kwargs["initial_state"]
        assert isinstance(initial, dict)
        assert initial["task_mode"] == "inspection"
        calls.append("specialized_runner")
        return SpecializedWorkflowRunResult(
            state=SpecializedWorkflowState(
                task_mode="inspection",
                status="completed",
                inspection_response="独立项目检查完成。",
                trace=["analyze_project", "report_project"],
            ),
            thread_id="specialized-thread",
        )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=legacy_bootstrap,  # type: ignore[arg-type]
        workflow_runner=legacy_runner,
        specialized_bootstrap_factory=specialized_bootstrap,  # type: ignore[arg-type]
        specialized_workflow_runner=specialized_runner,
        persistence=persistence,
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

    assert response.status_code == 200
    assert "独立项目检查完成" in response.text
    assert calls == ["specialized_bootstrap", "specialized_runner"]
    assert persistence.runtime_configs[0]["workflow_family"] == (
        "project_inspection"
    )


def test_blank_display_question_overrides_an_incorrect_injected_route(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    calls: list[str] = []

    def specialized_bootstrap(**_: object) -> object:
        calls.append("specialized_bootstrap")
        return object()

    def specialized_runner(**kwargs: object) -> SpecializedWorkflowRunResult:
        initial = kwargs["initial_state"]
        assert isinstance(initial, dict)
        assert initial["task_mode"] == "inspection"
        calls.append("specialized_runner")
        return SpecializedWorkflowRunResult(
            state=SpecializedWorkflowState(
                task_mode="inspection",
                status="completed",
                inspection_response="已进入屏幕故障诊断。",
                trace=["analyze_project", "report_project"],
            ),
            thread_id="display-diagnosis-thread",
        )

    app = create_app(
        projects_roots=[tmp_path],
        specialized_bootstrap_factory=specialized_bootstrap,
        specialized_workflow_runner=specialized_runner,
        conversation_router=DeepSeekConversationRouter(
            FakeJsonCompletionClient(
                [{"intent": "knowledge_task", "response": "不相关"}]
            ),
            "fast-model",
        ),
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "那为什么屏幕还是没亮"},
    )

    assert response.status_code == 200
    assert "已进入屏幕故障诊断" in response.text
    assert calls == ["specialized_bootstrap", "specialized_runner"]


def test_pdf_page_progress_is_streamed_as_structured_sse(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)

    def specialized_runner(**kwargs: object) -> SpecializedWorkflowRunResult:
        reporter = kwargs["progress_reporter"]
        assert callable(reporter)
        reporter(PdfSpecializedWorkflowProgress(
            stage="knowledge",
            message="已读取 12/36 页",
            progress_type="pdf",
            current=12,
            total=36,
            unit="pages",
            phase="extracting",
            batch=1,
        ))
        return SpecializedWorkflowRunResult(
            state=SpecializedWorkflowState(
                task_mode="knowledge",
                status="completed",
                knowledge_result={
                    "read_pdf": True,
                    "title": "ESP32 手册",
                    "total_pages": 36,
                    "batches": 3,
                    "preview": "GPIO",
                },
                trace=["execute_knowledge_task", "completed"],
            ),
            thread_id="pdf-progress-thread",
        )

    app = create_app(
        projects_roots=[tmp_path],
        specialized_bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        specialized_workflow_runner=specialized_runner,
        conversation_router=DeepSeekConversationRouter(
            FakeJsonCompletionClient(
                [{"intent": "knowledge_task", "response": ""}]
            ),
            "fast-model",
        ),
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "读取项目中的 ESP32 手册 PDF"},
    )

    progress = next(
        data
        for event, data in parse_sse(response.text)
        if event == "progress"
        and isinstance(data, dict)
        and data.get("progress_type") == "pdf"
    )
    assert progress == {
        "stage": "knowledge",
        "message": "已读取 12/36 页",
        "attempts": 0,
        "progress_type": "pdf",
        "current": 12,
        "total": 36,
        "unit": "pages",
        "phase": "extracting",
        "batch": 1,
    }


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


def test_web_explicit_supervisor_mode_uses_agent_entrypoint(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    calls: dict[str, object] = {}
    agent_context = object()

    def legacy_bootstrap(**kwargs: object) -> object:
        raise AssertionError("firmware task must not use legacy bootstrap")

    def legacy_runner(**kwargs: object) -> WorkflowRunResult:
        raise AssertionError("firmware task must not use legacy runner")

    def agent_bootstrap(**kwargs: object) -> object:
        calls["bootstrap"] = kwargs
        return agent_context

    def agent_runner(**kwargs: object) -> AgentWorkflowRunResult:
        calls["runner"] = kwargs
        reporter = kwargs["progress_reporter"]
        assert callable(reporter)
        reporter(
            AgentWorkflowProgress(
                node="project_inspector",
                message="已检查工程结构和现有能力",
                step_count=2,
                phase="completed",
                narrative="**第 2 轮｜已检查工程结构和现有能力**\n\n",
                tools=("project.inspect",),
            )
        )
        return _agent_run_result(
            {
                "status": "completed",
                "evidence_ids": ["build:web-agent"],
                "build_verified": True,
                "acceptance_passed": True,
                "trace": ["complete_objective"],
            }
        )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=legacy_bootstrap,  # type: ignore[arg-type]
        workflow_runner=legacy_runner,
        agent_bootstrap_factory=agent_bootstrap,  # type: ignore[arg-type]
        agent_workflow_runner=agent_runner,
        agent_runtime_mode="supervisor",
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "构建当前工程", "target_chip": "esp32"},
    )

    events = parse_sse(response.text)
    result = next(data for event, data in events if event == "result")
    progress = next(data for event, data in events if event == "progress")
    assert response.status_code == 200
    assert result["status"] == "completed"
    assert result["build_verified"] is True
    assert progress == {
        "stage": "project_inspector",
        "message": "已检查工程结构和现有能力",
        "attempts": 2,
        "phase": "completed",
        "tools": ["project.inspect"],
        "task_id": None,
    }
    token_text = "".join(
        data["token"]
        for event, data in events
        if event == "token" and isinstance(data, dict)
    )
    assert "第 2 轮｜已检查工程结构和现有能力" not in token_text
    assert "Supervisor 决策" not in token_text
    assert "目标：当前项目任务。" in token_text
    assert "计划：" in token_text
    bootstrap = calls["bootstrap"]
    runner = calls["runner"]
    assert isinstance(bootstrap, dict)
    assert isinstance(runner, dict)
    assert bootstrap["project_path"] == project.resolve()
    assert runner["context"] is agent_context
    assert runner["initial_state"]["target_chip"] == "esp32"
    assert runner["project_key"] == "0:blink"


def test_web_supervisor_emits_heartbeat_while_runner_is_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_project(tmp_path)
    monkeypatch.setattr("luxar.web._WORKFLOW_HEARTBEAT_SECONDS", 0.01)

    def agent_runner(**kwargs: object) -> AgentWorkflowRunResult:
        reporter = kwargs["progress_reporter"]
        assert callable(reporter)
        reporter(
            AgentWorkflowProgress(
                node="project_inspector",
                message="正在检查工程",
                step_count=1,
                phase="started",
                tools=("project.inspect",),
                task_id="inspect-project",
            )
        )
        time.sleep(0.04)
        return _agent_run_result(
            {
                "status": "completed",
                "acceptance_passed": True,
                "trace": [],
            }
        )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        agent_bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        agent_workflow_runner=agent_runner,
        agent_runtime_mode="supervisor",
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "执行耗时验证"},
    )

    heartbeats = [
        data
        for event, data in parse_sse(response.text)
        if event == "progress"
        and isinstance(data, dict)
        and data.get("phase") == "heartbeat"
    ]
    assert response.status_code == 200
    assert heartbeats
    assert heartbeats[0]["stage"] == "project_inspector"
    assert heartbeats[0]["tools"] == ["project.inspect"]
    assert heartbeats[0]["task_id"] == "inspect-project"


def test_web_supervisor_natural_language_run_persists_agent_snapshot(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    persistence = TransientPersistence()
    objective_id = "web-natural-gpio"
    client = FakeJsonCompletionClient(
        [
            {
                "intent": "change_objective",
                "objective": {
                    "objective_id": objective_id,
                    "title": "新增 GPIO13 高电平输出",
                    "description": "在当前工程新增 GPIO13 高电平输出",
                    "acceptance_criteria": ["GPIO13 输出高电平且构建通过"],
                },
                "change_set": {
                    "changes": [
                        {
                            "operation": "add",
                            "capability_id": "gpio.output:P13",
                            "desired_state": {
                                "pin": 13,
                                "mode": "output",
                                "level": 1,
                            },
                            "rationale": "用户要求新增 GPIO13 输出",
                        }
                    ]
                },
                "allowed_paths_by_capability": {
                    "gpio.output:P13": ["main/main.c"]
                },
                "objective_changed": True,
            },
            {
                "bundle_id": "web-natural-gpio-bundle",
                "task_id": f"{objective_id}:code:add:gpio.output_P13",
                "description": "创建 GPIO13 高电平输出入口",
                "allowed_paths": ["main/main.c"],
                "preserves": [],
                "changes": [
                    {
                        "operation": "create",
                        "path": "main/main.c",
                        "content": (
                            '#include "driver/gpio.h"\n'
                            "void app_main(void) {\n"
                            "    gpio_set_direction(GPIO_NUM_13, GPIO_MODE_OUTPUT);\n"
                            "    gpio_set_level(GPIO_NUM_13, 1);\n"
                            "}\n"
                        ),
                    }
                ],
            },
        ]
    )
    context = build_deepseek_agent_runtime_context(
        project_path=project,
        build_executor=FakeEspIdf(
            [
                BuildEvidence(
                    success=True,
                    command=["idf.py", "build"],
                    return_code=0,
                )
            ]
        ),
        settings=DeepSeekSettings(
            api_key=SecretStr("test-key"),
            repair_model="deepseek-reasoner",
        ),
        client=client,
    )
    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        agent_bootstrap_factory=lambda **_: context,  # type: ignore[arg-type]
        agent_runtime_mode="supervisor",
        persistence=persistence,
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "实现一个高电平状态指示灯功能"},
    )

    events = parse_sse(response.text)
    result = next(data for event, data in events if event == "result")
    progress_events = [
        data
        for event, data in events
        if event == "progress" and isinstance(data, dict)
    ]
    token_text = "".join(
        data["token"]
        for event, data in events
        if event == "token" and isinstance(data, dict)
    )
    snapshot = TestClient(app).get("/api/projects/blink/agent").json()
    assert response.status_code == 200
    assert result["last_error"] is None
    assert result["status"] == "completed"
    assert result["build_verified"] is True
    assert (project / "main" / "main.c").is_file()
    assert snapshot["status"] == "completed"
    assert snapshot["objective"]["objective_id"] == objective_id
    assert len(progress_events) > 4
    assert any(item["phase"] == "decision" for item in progress_events)
    assert any(item["tools"] for item in progress_events)
    assert "Supervisor 决策" not in token_text
    assert "准备调用：" not in token_text
    assert "本步调用完成：" not in token_text
    assert "执行计划已生成" not in token_text
    assert "第 1 轮" not in token_text
    assert "目标：新增 GPIO13 高电平输出。" in token_text
    assert "计划：" in token_text
    assert "检查现有工程" in token_text
    assert "建立变更边界" in token_text
    assert "验证目标和非回归条件" in token_text
    assert "本次修改：" in token_text
    assert "create: main/main.c" in token_text
    assert "验证结果：" in token_text
    assert snapshot["acceptance_passed"] is True
    assert "bundle:web-natural-gpio-bundle" in {
        item["evidence_id"] for item in snapshot["evidence"]
    }
    assert len(client.calls) == 2


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
    persistence = TransientPersistence()

    def fail_bootstrap(**_: object) -> object:
        raise ValueError("SECRET_API_KEY_DETAIL")

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=fail_bootstrap,  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        persistence=persistence,
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
    assert next(iter(persistence._runs.values()))["status"] == "failed"


def test_web_classifies_workflow_validation_separately_from_startup(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    persistence = TransientPersistence()

    def fail_workflow(**_: object) -> WorkflowRunResult:
        raise ValueError("SECRET_MODEL_RESPONSE_DETAIL")

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=fail_workflow,
        persistence=persistence,
    )
    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": "P34 输出低电平"},
    )

    events = parse_sse(response.text)
    error_events = [data for event, data in events if event == "error"]
    assert error_events == [{
        "category": "validation",
        "message": "任务处理结果校验失败，请检查服务端日志",
    }]
    assert events[-1] == ("done", "[DONE]")
    assert "SECRET_MODEL_RESPONSE_DETAIL" not in response.text
    assert next(iter(persistence._runs.values()))["status"] == "failed"


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


def test_pdf_knowledge_result_is_streamed_and_persisted(tmp_path: Path) -> None:
    make_project(tmp_path)

    def knowledge_runner(**kwargs: object) -> WorkflowRunResult:
        initial = kwargs["initial_state"]
        assert isinstance(initial, dict)
        assert initial["task_mode"] == "knowledge"
        return _run_result(
            WorkflowState(
                status="completed",
                knowledge_result={
                    "read_pdf": True,
                    "title": "OLED 规格书",
                    "total_pages": 37,
                    "batches": 4,
                    "characters": 121249,
                    "preview": "## 第 1 页\nOLED Product Specification",
                },
            )
        )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=knowledge_runner,
        conversation_router=DeepSeekConversationRouter(
            FakeJsonCompletionClient(
                [{"intent": "knowledge_task", "response": ""}]
            ),
            "fast-model",
        ),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/conversations/blink",
            json={"message": "读取 OLED 规格书 PDF"},
        )
        events = parse_sse(response.text)
        streamed = "".join(
            data["token"]
            for event, data in events
            if event == "token" and isinstance(data, dict)
        )
        history = client.get("/api/conversations/blink").json()["messages"]

    assert "PDF 已完整分批读取：共 37 页" in streamed
    assert "OLED Product Specification" in streamed
    assert "本次没有修改源码" not in streamed
    assert "PDF 已完整分批读取：共 37 页" in history[1]["content"]
    assert "OLED Product Specification" in history[1]["content"]


def test_explicit_absolute_pdf_command_overrides_wrong_firmware_router(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    router_calls: list[str] = []

    class WrongRouter:
        def route(self, message: str, *_: object, **__: object) -> ConversationDecision:
            router_calls.append(message)
            return ConversationDecision(intent="firmware_task")

    def knowledge_runner(**kwargs: object) -> WorkflowRunResult:
        initial = kwargs["initial_state"]
        assert isinstance(initial, dict)
        assert initial["task_mode"] == "knowledge"
        return _run_result(
            WorkflowState(
                status="completed",
                knowledge_result={
                    "read_pdf": True,
                    "title": "1.3寸横屏规格书",
                    "total_pages": 37,
                    "batches": 4,
                    "characters": 1000,
                    "preview": "OLED specification",
                },
            )
        )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=knowledge_runner,
        conversation_router=WrongRouter(),  # type: ignore[arg-type]
        continuous_agent_enabled=True,
    )
    message = (
        '"D:\\download\\中景园电子1.3英寸OLED技术资料V3.0\\'
        '1.3寸横屏规格书.pdf" 那么读取这个PDF'
    )

    response = TestClient(app).post(
        "/api/conversations/blink",
        json={"message": message},
    )

    assert response.status_code == 200
    assert router_calls == []
    assert "正在读取你指定的本地" in response.text
    assert "检索项目知识库" not in response.text
    assert "PDF 已完整分批读取：共 37 页" in response.text


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


def test_web_supervisor_approval_flow_uses_agent_resumer(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)
    persistence = TransientPersistence()
    context = object()
    saver = InMemorySaver()
    resume_calls: list[dict[str, object]] = []
    request = AgentApprovalRequest(
        task_id="agent-web:architecture",
        title="审批架构任务",
        summary="高风险任务需要明确批准",
        operation="project.plan",
        risks=["可能修改工程状态"],
    )

    def agent_runner(**kwargs: object) -> AgentWorkflowRunResult:
        return AgentWorkflowRunResult(
            state={
                "status": "awaiting_user",
                "approval_request": request,
                "approval_status": "pending",
                "trace": [],
            },
            thread_id="agent-web-thread",
            pending_approval=request,
            checkpointer=saver,
        )

    def agent_resumer(**kwargs: object) -> AgentWorkflowRunResult:
        assert persistence._approvals["0:blink"].status == "completed"
        resume_calls.append(kwargs)
        return AgentWorkflowRunResult(
            state={
                "status": "completed",
                "approval_request": request,
                "approval_status": "approved",
                "evidence_ids": ["approval:agent-web:architecture"],
                "acceptance_passed": True,
                "trace": [],
            },
            thread_id="agent-web-thread",
            checkpointer=saver,
        )

    app = create_app(
        projects_roots=[tmp_path],
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        workflow_runner=lambda **_: _run_result(WorkflowState()),
        agent_bootstrap_factory=lambda **_: context,  # type: ignore[arg-type]
        agent_workflow_runner=agent_runner,
        agent_workflow_resumer=agent_resumer,
        agent_runtime_mode="supervisor",
        persistence=persistence,
    )
    client = TestClient(app)
    thread, responses = _run_task_in_background(client)
    _wait_for_pending_approval(app)

    decision = client.post(
        "/api/conversations/blink/approval",
        json={"decision": "approve", "feedback": "确认执行"},
    )
    thread.join(timeout=5)

    assert decision.status_code == 200
    events = parse_sse(responses[0].text)
    approval = next(data for event, data in events if event == "approval")
    result = next(data for event, data in events if event == "result")
    assert approval["request"]["kind"] == "task_approval"
    assert approval["request"]["task_id"] == "agent-web:architecture"
    assert result["status"] == "completed"
    assert resume_calls[0]["thread_id"] == "agent-web-thread"
    assert resume_calls[0]["approved"] is True
    assert resume_calls[0]["feedback"] == "确认执行"
    assert resume_calls[0]["checkpointer"] is saver
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
        audit_response = client.get("/api/runtime/audit")
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
        "driver_library_path": str(
            (tmp_path.parent / ".luxar-data" / "driver-library").resolve()
        ),
    }
    audit = audit_response.json()
    assert audit["durable"] is True
    assert audit["checkpoint_inventory_complete"] is True
    assert audit["qualifies_as_release_evidence"] is False
    assert "observation_window_too_short" in audit["blocking_reasons"]


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


def test_restart_path_resumes_persisted_supervisor_approval(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)

    class DurableTestPersistence(TransientPersistence):
        durable = True

    persistence = DurableTestPersistence()
    persistence.start_run(
        thread_id="agent-durable-thread",
        task_key="0:blink",
        project_name="blink",
        root_index=0,
        task_text="执行高风险任务",
        runtime_config={"agent_runtime": "supervisor"},
    )
    persistence.start_conversation_stream(
        thread_id="agent-durable-thread",
        task_key="0:blink",
        user_message="执行高风险任务",
    )
    persistence.append_conversation_stream_event(
        "agent-durable-thread",
        event="token",
        data={"token": "已完成风险检查。"},
    )
    persistence.finish_conversation_stream(
        "agent-durable-thread", status="pending_approval"
    )
    request = AgentApprovalRequest(
        task_id="agent-durable:task",
        title="审批高风险任务",
        summary="需要明确批准后继续",
        operation="device.flash",
        risks=["会修改外部设备状态"],
    )
    persistence.save_pending_approval(
        PendingApprovalRecord(
            task_key="0:blink",
            project_name="blink",
            root_index=0,
            thread_id="agent-durable-thread",
            request=request.model_dump(mode="json"),
            runtime_config={
                "agent_runtime": "supervisor",
                "task_text": "执行高风险任务",
                "serial_port": "COM3",
                "target_chip": "esp32",
            },
        )
    )
    checkpointer = object()
    context = object()
    bootstrap_calls: list[dict[str, object]] = []
    resume_calls: list[dict[str, object]] = []

    def fake_agent_resume(**kwargs: object) -> AgentWorkflowRunResult:
        resume_calls.append(kwargs)
        return AgentWorkflowRunResult(
            state={
                "status": "completed",
                "approval_status": "approved",
                "evidence_ids": ["approval:agent-durable:task"],
                "acceptance_passed": True,
                "trace": [],
            },
            thread_id="agent-durable-thread",
        )

    app = create_app(
        projects_roots=[tmp_path],
        persistence=persistence,
        checkpointer=checkpointer,  # type: ignore[arg-type]
        bootstrap_factory=lambda **_: object(),  # type: ignore[arg-type]
        agent_bootstrap_factory=lambda **kwargs: (
            bootstrap_calls.append(kwargs) or context
        ),  # type: ignore[arg-type]
        agent_workflow_resumer=fake_agent_resume,
        agent_runtime_mode="supervisor",
    )

    response = TestClient(app).post(
        "/api/conversations/blink/approval",
        json={"decision": "approve", "feedback": "设备已核对"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["recovered"] is True
    assert payload["result"]["status"] == "completed"
    assert bootstrap_calls[0]["project_path"] == (tmp_path / "blink").resolve()
    assert resume_calls[0]["thread_id"] == "agent-durable-thread"
    assert resume_calls[0]["context"] is context
    assert resume_calls[0]["checkpointer"] is checkpointer
    assert resume_calls[0]["approved"] is True
    assert resume_calls[0]["feedback"] == "设备已核对"
    assert persistence.get_messages("0:blink")[0]["content"] == "执行高风险任务"
    stream = persistence.get_conversation_stream("agent-durable-thread")
    assert stream is not None
    assert stream.status == "completed"
    assert "已完成风险检查" in stream.assistant_content
    assert "审批已处理" in stream.assistant_content
    assert [
        item.event
        for item in persistence.list_conversation_stream_events(
            "agent-durable-thread"
        )[-2:]
    ] == ["result", "done"]


def test_restart_path_resumes_persisted_specialized_approval(
    tmp_path: Path,
) -> None:
    make_project(tmp_path)

    class DurableTestPersistence(TransientPersistence):
        durable = True

    persistence = DurableTestPersistence()
    request = WorkflowInteraction(
        kind="knowledge_write",
        title="确认知识库变更",
        summary="保存 OLED 笔记",
        options=["批准执行", "取消任务"],
        operation={"action": "upsert"},
    )
    runtime_config = {
        "agent_runtime": "legacy",
        "firmware_runtime": "supervisor",
        "workflow_family": "knowledge_task",
        "task_text": "保存 OLED 笔记",
        "target_chip": "esp32",
    }
    persistence.start_run(
        thread_id="specialized-durable-thread",
        task_key="0:blink",
        project_name="blink",
        root_index=0,
        task_text="保存 OLED 笔记",
        runtime_config=runtime_config,
    )
    persistence.save_pending_approval(
        PendingApprovalRecord(
            task_key="0:blink",
            project_name="blink",
            root_index=0,
            thread_id="specialized-durable-thread",
            request=request.model_dump(mode="json"),
            runtime_config=runtime_config,
        )
    )
    bootstrap_calls: list[dict[str, object]] = []
    resume_calls: list[dict[str, object]] = []

    def specialized_bootstrap(**kwargs: object) -> object:
        bootstrap_calls.append(kwargs)
        return object()

    def specialized_resume(**kwargs: object) -> SpecializedWorkflowRunResult:
        resume_calls.append(kwargs)
        return SpecializedWorkflowRunResult(
            state=SpecializedWorkflowState(
                task_mode="knowledge",
                status="completed",
                knowledge_result={"document_id": "doc-1", "chunks": 2},
                trace=[
                    "analyze_knowledge_task",
                    "review_knowledge_task",
                    "execute_knowledge_task",
                    "completed",
                ],
            ),
            thread_id="specialized-durable-thread",
        )

    app = create_app(
        projects_roots=[tmp_path],
        specialized_bootstrap_factory=specialized_bootstrap,  # type: ignore[arg-type]
        specialized_workflow_resumer=specialized_resume,
        persistence=persistence,
        checkpointer=object(),  # type: ignore[arg-type]
    )

    response = TestClient(app).post(
        "/api/conversations/blink/approval",
        json={"decision": "approve"},
    )

    assert response.status_code == 200
    assert response.json()["recovered"] is True
    assert len(bootstrap_calls) == 1
    assert bootstrap_calls[0]["knowledge_service"] is app.state.knowledge_service
    assert len(resume_calls) == 1
    assert resume_calls[0]["thread_id"] == "specialized-durable-thread"
    assert persistence.get_pending_approval("0:blink") is None


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
