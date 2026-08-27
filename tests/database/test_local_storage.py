from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from luxar.database import (
    LocalStorageRuntime,
    LocalStorageSettings,
    SQLitePersistence,
)
from luxar.database.persistence import PendingApprovalRecord
from luxar.lance_knowledge import LanceDBKnowledgeIndex


def test_project_root_default_is_independent_of_working_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    projects = repository / "projects"
    projects.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.delenv("LUXAR_STORAGE_DIRECTORY", raising=False)

    monkeypatch.chdir(elsewhere)
    settings = LocalStorageSettings.for_projects_root(projects)

    assert settings.root == repository / ".luxar-data"


def test_explicit_storage_directory_overrides_project_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    configured = tmp_path / "configured-storage"
    monkeypatch.setenv("LUXAR_STORAGE_DIRECTORY", str(configured))

    settings = LocalStorageSettings.for_projects_root(
        tmp_path / "repository" / "projects"
    )

    assert settings.root == configured.resolve()


def test_relative_storage_setting_is_anchored_beside_projects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    projects = repository / "projects"
    elsewhere = tmp_path / "elsewhere"
    projects.mkdir(parents=True)
    elsewhere.mkdir()
    monkeypatch.setenv("LUXAR_STORAGE_DIRECTORY", ".custom-data")
    monkeypatch.chdir(elsewhere)

    settings = LocalStorageSettings.for_projects_root(projects)

    assert settings.root == repository / ".custom-data"


def test_sqlite_repository_survives_reopen(tmp_path: Path) -> None:
    database = tmp_path / "luxar.sqlite3"
    repository = SQLitePersistence(database)
    repository.start_run(
        thread_id="thread-1",
        task_key="0:test",
        project_name="test",
        root_index=0,
        task_text="build",
        runtime_config={"target_chip": "esp32"},
    )
    repository.append_exchange(
        "0:test",
        thread_id="thread-1",
        user_message="build",
        assistant_message="completed",
    )
    repository.upsert_memory(
        project_key="0:test",
        memory_key="device.target_chip",
        memory_type="device_config",
        value={"target_chip": "esp32"},
        source_thread_id="thread-1",
    )
    repository.save_pending_approval(
        PendingApprovalRecord(
            task_key="0:test",
            project_name="test",
            root_index=0,
            thread_id="thread-1",
            request={"port": "COM3"},
            runtime_config={},
        )
    )

    reopened = SQLitePersistence(database)
    assert reopened.get_messages("0:test")[-1]["content"] == "completed"
    assert reopened.find_memories("0:test")[0].value == {
        "target_chip": "esp32"
    }
    assert reopened.get_pending_approval("0:test") is not None


def test_sqlite_conversation_stream_survives_reopen_and_resumes_by_sequence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "conversation-stream.sqlite3"
    repository = SQLitePersistence(database)
    repository.start_conversation_stream(
        thread_id="stream-1",
        task_key="0:test",
        user_message="继续",
    )
    repository.append_conversation_stream_event(
        "stream-1", event="token", data={"token": "第一步完成。"}
    )
    checkpoint = repository.append_conversation_stream_event(
        "stream-1",
        event="tool_call",
        data={"tool_call": "workspace_build"},
    )
    repository.append_conversation_stream_event(
        "stream-1", event="token", data={"token": "开始构建。"}
    )

    reopened = SQLitePersistence(database)
    active = reopened.get_active_conversation_stream("0:test")
    assert active is not None
    assert active.user_message == "继续"
    assert active.assistant_content == "第一步完成。开始构建。"
    replay = reopened.list_conversation_stream_events(
        "stream-1", after_sequence=checkpoint
    )
    assert [(item.sequence, item.event, item.data) for item in replay] == [
        (checkpoint + 1, "token", {"token": "开始构建。"})
    ]


def test_sqlite_returns_latest_completed_run_and_skips_incomplete_run(
    tmp_path: Path,
) -> None:
    repository = SQLitePersistence(tmp_path / "luxar.sqlite3")
    for thread_id, status in (("completed", "completed"), ("later", "failed")):
        repository.start_run(
            thread_id=thread_id,
            task_key="0:test",
            project_name="test",
            root_index=0,
            task_text=thread_id,
            runtime_config={},
        )
        repository.finish_run(
            thread_id,
            status=status,
            result={"changed_files": ["main/main.c"]},
        )

    latest = repository.get_latest_completed_run("0:test")
    latest_any_status = repository.get_latest_run("0:test")

    assert latest is not None
    assert latest.thread_id == "completed"
    assert latest.result["changed_files"] == ["main/main.c"]
    assert latest_any_status is not None
    assert latest_any_status.thread_id == "later"
    assert latest_any_status.status == "failed"


def test_sqlite_latest_run_exposes_unfinished_workflow_family(
    tmp_path: Path,
) -> None:
    repository = SQLitePersistence(tmp_path / "luxar-running.sqlite3")
    repository.start_run(
        thread_id="knowledge-running",
        task_key="0:test",
        project_name="test",
        root_index=0,
        task_text="读取文档",
        runtime_config={"workflow_family": "knowledge_task"},
    )

    latest = repository.get_latest_run("0:test")

    assert latest is not None
    assert latest.status == "running"
    assert latest.result == {}
    assert latest.workflow_family == "knowledge_task"


def test_message_import_is_atomic_and_idempotent(tmp_path: Path) -> None:
    repository = SQLitePersistence(tmp_path / "luxar.sqlite3")
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    first = repository.import_messages_once(
        "source-a:0:test",
        "0:test",
        messages,
        thread_id="migration-a",
    )
    second = repository.import_messages_once(
        "source-a:0:test",
        "0:test",
        messages,
        thread_id="migration-a",
    )

    assert first == 2
    assert second == 0
    assert repository.get_messages("0:test") == messages


def test_sqlite_checkpointer_resumes_after_runtime_restart(tmp_path: Path) -> None:
    settings = LocalStorageSettings(directory=tmp_path)
    thread_id = f"restart:{uuid.uuid4().hex}"

    def approval_node(state: dict[str, object]) -> dict[str, object]:
        decision = interrupt({"kind": "approval"})
        return {**state, "approved": bool(decision["approved"])}

    builder = StateGraph(dict)
    builder.add_node("approval", approval_node)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    config = {"configurable": {"thread_id": thread_id}}

    first = LocalStorageRuntime(settings)
    first.open()
    try:
        graph = builder.compile(checkpointer=first.checkpointer())
        snapshots = list(graph.stream({}, config=config, stream_mode="values"))
        assert "__interrupt__" in snapshots[-1]
    finally:
        first.close()

    second = LocalStorageRuntime(settings)
    second.open()
    try:
        graph = builder.compile(checkpointer=second.checkpointer())
        result = graph.invoke(
            Command(resume={"approved": True}),
            config=config,
        )
        assert result["approved"] is True
    finally:
        second.close()


def test_lancedb_knowledge_index_persists_and_filters_projects(
    tmp_path: Path,
) -> None:
    index = LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=3)
    index.replace_document(
        document_id="doc-a",
        project_key="0:test",
        source_uri="manual://esp-idf",
        title="ESP-IDF manual",
        content_hash="a",
        metadata={},
        chunks=[("idf.py build compiles firmware", 4, [1.0, 0.0, 0.0])],
    )
    index.replace_document(
        document_id="doc-b",
        project_key="0:other",
        source_uri="manual://other",
        title="Other",
        content_hash="b",
        metadata={},
        chunks=[("unrelated", 1, [1.0, 0.0, 0.0])],
    )

    reopened = LanceDBKnowledgeIndex(
        tmp_path / "knowledge.lance", dimensions=3
    )
    matches = reopened.search_knowledge(
        project_key="0:test",
        query_text="build",
        query_embedding=[1.0, 0.0, 0.0],
        limit=3,
    )
    assert reopened.count_knowledge_documents("0:test") == 1
    assert [match.document_id for match in matches] == ["doc-a"]


def test_lancedb_empty_index_rebuilds_for_new_embedding_dimensions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "knowledge.lance"
    LanceDBKnowledgeIndex(path, dimensions=3)

    reopened = LanceDBKnowledgeIndex(path, dimensions=5)

    vector_type = reopened._db.open_table(reopened._CHUNKS).schema.field(
        "vector"
    ).type
    assert vector_type.list_size == 5
    assert reopened.search_knowledge(
        project_key="0:test",
        query_text="empty",
        query_embedding=[0.0] * 5,
    ) == []


def test_lancedb_populated_index_rejects_dimension_change(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.lance"
    index = LanceDBKnowledgeIndex(path, dimensions=3)
    index.replace_document(
        document_id="doc-a",
        project_key="0:test",
        source_uri="manual://esp-idf",
        title="ESP-IDF manual",
        content_hash="a",
        metadata={},
        chunks=[("idf.py build", 2, [1.0, 0.0, 0.0])],
    )

    with pytest.raises(ValueError, match="重新索引"):
        LanceDBKnowledgeIndex(path, dimensions=5)
