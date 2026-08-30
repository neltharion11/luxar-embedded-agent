"""Opt-in real PostgreSQL + pgvector durability tests.

Set LUXAR_TEST_DATABASE_URL to a disposable database. The default suite never
connects to a database or mutates external state.
"""

from __future__ import annotations

import os
import uuid

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from luxar.database import DatabaseRuntime, DatabaseSettings, PostgresPersistence
from luxar.database.persistence import PendingApprovalRecord


DATABASE_URL = os.getenv("LUXAR_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="set LUXAR_TEST_DATABASE_URL to run PostgreSQL integration tests",
)


def _runtime() -> DatabaseRuntime:
    return DatabaseRuntime(
        DatabaseSettings(
            url=DATABASE_URL,
            auto_migrate=True,
            require_vector=True,
            min_pool_size=1,
            max_pool_size=2,
        )
    )


def test_postgres_repository_round_trip_and_pgvector_search() -> None:
    unique = uuid.uuid4().hex
    task_key = f"test:{unique}"
    thread_id = f"thread:{unique}"
    runtime = _runtime()
    runtime.open()
    try:
        repository = PostgresPersistence(runtime.pool)
        repository.start_run(
            thread_id=thread_id,
            task_key=task_key,
            project_name="blink",
            root_index=0,
            task_text="flash",
            runtime_config={"serial_port": "COM3"},
        )
        repository.save_pending_approval(
            PendingApprovalRecord(
                task_key=task_key,
                project_name="blink",
                root_index=0,
                thread_id=thread_id,
                request={"port": "COM3"},
                runtime_config={"serial_port": "COM3"},
            )
        )
        assert repository.get_pending_approval(task_key) is not None
        assert repository.decide_approval(task_key, True) is True
        repository.complete_approval(task_key)
        repository.append_exchange(
            task_key,
            thread_id=thread_id,
            user_message="flash",
            assistant_message="completed",
        )
        repository.append_exchange(
            task_key,
            thread_id=thread_id,
            user_message="flash",
            assistant_message="completed after retry",
        )
        assert repository.get_messages(task_key) == [
            {"role": "user", "content": "flash"},
            {"role": "assistant", "content": "completed after retry"},
        ]
        repository.finish_run(
            thread_id,
            status="completed",
            result={"status": "completed"},
        )
        with runtime.pool.connection() as connection:
            run_status = connection.execute(
                "SELECT status FROM luxar_workflow_runs WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()[0]
            approval_status = connection.execute(
                "SELECT status FROM luxar_approval_requests WHERE task_key = %s",
                (task_key,),
            ).fetchone()[0]
        assert run_status == "completed"
        assert approval_status == "completed"
        repository.upsert_memory(
            project_key=task_key,
            memory_key="device.target",
            memory_type="device_config",
            value={"target_chip": "esp32"},
            source_thread_id=thread_id,
        )
        assert repository.find_memories(task_key)[0].memory_key == "device.target"

        vector = [0.0] * 1536
        vector[0] = 1.0
        repository.replace_document(
            document_id=str(uuid.uuid4()),
            project_key=task_key,
            source_uri="test://manual",
            title="manual",
            content_hash=unique,
            metadata={},
            chunks=[("idf.py build", 3, vector)],
        )
        matches = repository.search_knowledge(
            project_key=task_key,
            query_text="build",
            query_embedding=vector,
            limit=1,
        )
        assert matches[0].source_uri == "test://manual"
    finally:
        runtime.close()


def test_checkpoint_interrupt_resumes_after_runtime_restart() -> None:
    thread_id = f"restart:{uuid.uuid4().hex}"

    def approval_node(state: dict[str, object]) -> dict[str, object]:
        decision = interrupt({"kind": "approval"})
        return {**state, "approved": bool(decision["approved"])}

    builder = StateGraph(dict)
    builder.add_node("approval", approval_node)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    config = {"configurable": {"thread_id": thread_id}}

    first = _runtime()
    first.open()
    try:
        graph = builder.compile(checkpointer=first.checkpointer())
        snapshots = list(graph.stream({}, config=config, stream_mode="values"))
        assert "__interrupt__" in snapshots[-1]
    finally:
        first.close()

    second = _runtime()
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
