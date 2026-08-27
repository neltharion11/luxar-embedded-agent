from __future__ import annotations

import importlib
import logging
import sqlite3
import uuid
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from luxar.checkpoint_serde import (
    LUXAR_ALLOWED_MSGPACK_MODULES,
    create_checkpoint_serializer,
)
from luxar.database import LocalStorageRuntime, LocalStorageSettings
from luxar.domain.repairs import ProjectFile
from luxar.domain.continuous_agent.events import ConversationEvent


class UnregisteredCheckpointModel(BaseModel):
    value: str


def test_checkpoint_allowlist_contains_real_pydantic_models() -> None:
    for module_name, class_name in LUXAR_ALLOWED_MSGPACK_MODULES:
        module = importlib.import_module(module_name)
        model = getattr(module, class_name)
        assert issubclass(model, BaseModel)


def test_allowed_luxar_model_round_trip_has_no_security_warning(
    caplog,
) -> None:
    serializer = create_checkpoint_serializer()
    payload = serializer.dumps_typed(
        ProjectFile(path="main/main.c", content="void app_main(void) {}")
    )

    with caplog.at_level(logging.WARNING):
        restored = serializer.loads_typed(payload)

    assert restored == ProjectFile(
        path="main/main.c",
        content="void app_main(void) {}",
    )
    assert "unregistered type" not in caplog.text
    assert "Blocked deserialization" not in caplog.text


def test_continuous_agent_event_round_trips_through_strict_serializer() -> None:
    serializer = create_checkpoint_serializer()
    event = ConversationEvent(
        event_id="turn-1:user",
        turn_id="turn-1",
        kind="user_message",
        sequence=1,
        payload={"content": "COM4 好了"},
    )

    restored = serializer.loads_typed(serializer.dumps_typed(event))

    assert restored == event


def test_unregistered_model_is_not_reconstructed(caplog) -> None:
    serializer = create_checkpoint_serializer()
    payload = serializer.dumps_typed(UnregisteredCheckpointModel(value="unsafe"))

    with caplog.at_level(logging.WARNING):
        restored = serializer.loads_typed(payload)

    assert restored == {"value": "unsafe"}
    assert not isinstance(restored, UnregisteredCheckpointModel)
    assert "Blocked deserialization" in caplog.text


def test_strict_runtime_resumes_checkpoint_written_before_allowlist(
    tmp_path: Path,
    caplog,
) -> None:
    """Existing permissive checkpoints remain resumable after the upgrade."""

    settings = LocalStorageSettings(directory=tmp_path)
    settings.root.mkdir(parents=True, exist_ok=True)
    thread_id = f"legacy:{uuid.uuid4().hex}"

    def approval_node(state: dict[str, object]) -> dict[str, object]:
        decision = interrupt({"kind": "approval"})
        return {**state, "approved": bool(decision["approved"])}

    builder = StateGraph(dict)
    builder.add_node("approval", approval_node)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    config = {"configurable": {"thread_id": thread_id}}

    connection = sqlite3.connect(
        settings.checkpoint_path,
        check_same_thread=False,
    )
    try:
        legacy_saver = SqliteSaver(connection)
        legacy_saver.setup()
        legacy_graph = builder.compile(checkpointer=legacy_saver)
        snapshots = list(
            legacy_graph.stream(
                {"project_file": ProjectFile(path="main/main.c", content="old")},
                config=config,
                stream_mode="values",
            )
        )
        assert "__interrupt__" in snapshots[-1]
    finally:
        connection.close()

    runtime = LocalStorageRuntime(settings)
    runtime.open()
    try:
        graph = builder.compile(checkpointer=runtime.checkpointer())
        with caplog.at_level(logging.WARNING):
            result = graph.invoke(
                Command(resume={"approved": True}),
                config=config,
            )
    finally:
        runtime.close()

    assert result["approved"] is True
    assert result["project_file"] == ProjectFile(
        path="main/main.c",
        content="old",
    )
    assert "unregistered type" not in caplog.text
    assert "Blocked deserialization" not in caplog.text
