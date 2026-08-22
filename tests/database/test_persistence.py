from __future__ import annotations

from importlib.resources import files

import pytest

from luxar.database.persistence import (
    PendingApprovalRecord,
    TransientPersistence,
)
from luxar.database.settings import DatabaseSettings


def test_database_settings_are_optional_and_secret(monkeypatch) -> None:
    monkeypatch.delenv("LUXAR_DATABASE_URL", raising=False)
    assert DatabaseSettings().configured is False

    monkeypatch.setenv(
        "LUXAR_DATABASE_URL",
        "postgresql://user:secret@localhost/luxar",
    )
    settings = DatabaseSettings()
    assert settings.configured is True
    assert "secret" not in repr(settings)
    assert settings.connection_string().endswith("localhost/luxar")


def test_database_settings_reject_invalid_pool_bounds() -> None:
    with pytest.raises(ValueError):
        DatabaseSettings(min_pool_size=4, max_pool_size=2)


def test_versioned_migrations_cover_application_memory_and_vector() -> None:
    resources = files("luxar.database.migrations")
    application = resources.joinpath("001_application.sql").read_text("utf-8")
    knowledge = resources.joinpath("002_knowledge.sql").read_text("utf-8")
    assert "luxar_workflow_runs" in application
    assert "luxar_approval_requests" in application
    assert "luxar_project_memories" in application
    assert "CREATE EXTENSION IF NOT EXISTS vector" in knowledge
    assert "USING hnsw" in knowledge


def test_transient_repository_has_same_conversation_and_approval_contract() -> None:
    repository = TransientPersistence()
    repository.start_run(
        thread_id="t1",
        task_key="0:blink",
        project_name="blink",
        root_index=0,
        task_text="flash",
        runtime_config={},
    )
    repository.append_exchange(
        "0:blink",
        thread_id="t1",
        user_message="flash",
        assistant_message="completed",
    )
    assert repository.get_messages("0:blink") == [
        {"role": "user", "content": "flash"},
        {"role": "assistant", "content": "completed"},
    ]

    record = PendingApprovalRecord(
        task_key="0:blink",
        project_name="blink",
        root_index=0,
        thread_id="t1",
        request={"port": "COM3"},
        runtime_config={},
    )
    repository.save_pending_approval(record)
    assert repository.get_pending_approval("0:blink") == record
    assert repository.decide_approval("0:blink", True) is True
    assert repository.get_pending_approval("0:blink") is None


def test_transient_repository_returns_latest_completed_result() -> None:
    repository = TransientPersistence()
    repository.start_run(
        thread_id="run",
        task_key="0:blink",
        project_name="blink",
        root_index=0,
        task_text="P13 输出低电平",
        runtime_config={},
    )
    repository.finish_run(
        "run",
        status="completed",
        result={"flash_evidence": None},
    )

    latest = repository.get_latest_completed_run("0:blink")

    assert latest is not None
    assert latest.task_text == "P13 输出低电平"


def test_structured_memory_upsert_and_filter() -> None:
    repository = TransientPersistence()
    repository.upsert_memory(
        project_key="0:blink",
        memory_key="device.target",
        memory_type="device_config",
        value={"target_chip": "esp32"},
        confidence=0.9,
    )
    repository.upsert_memory(
        project_key="0:blink",
        memory_key="style.c",
        memory_type="code_style",
        value={"indent": 4},
    )
    memories = repository.find_memories(
        "0:blink", memory_type="device_config"
    )
    assert len(memories) == 1
    assert memories[0].value == {"target_chip": "esp32"}
