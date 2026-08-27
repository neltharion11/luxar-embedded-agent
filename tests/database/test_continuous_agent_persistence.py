from pathlib import Path

import pytest

from luxar.database import SQLitePersistence, TransientPersistence


@pytest.fixture(params=["transient", "sqlite"])
def repository(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> TransientPersistence | SQLitePersistence:
    if request.param == "sqlite":
        return SQLitePersistence(tmp_path / "application.db")
    return TransientPersistence()


def test_agent_session_and_turn_contract(
    repository: TransientPersistence | SQLitePersistence,
) -> None:
    session = repository.create_agent_session(
        session_id="session-1",
        project_key="0:test4",
    )
    turn = repository.start_agent_turn(
        turn_id="turn-1",
        session_id=session.session_id,
        client_turn_id="client-1",
        user_message="重新烧录",
    )
    replay = repository.start_agent_turn(
        turn_id="turn-ignored",
        session_id=session.session_id,
        client_turn_id="client-1",
        user_message="不会覆盖原消息",
    )

    assert repository.get_active_agent_session("0:test4") == session
    assert replay.turn_id == turn.turn_id
    assert replay.user_message == "重新烧录"

    projected = repository.update_agent_session_state(
        session.session_id,
        active_objective_id="objective-1",
        context_summary="正在烧录并验证设备",
        compaction_cursor=12,
    )
    assert projected.active_objective_id == "objective-1"
    assert projected.context_summary == "正在烧录并验证设备"
    assert projected.compaction_cursor == 12

    repository.finish_agent_turn(
        turn.turn_id,
        status="waiting_input",
        assistant_message="请提供串口",
        failure={"category": "user_input", "missing": ["serial_port"]},
    )
    restored = repository.get_agent_turn(turn.turn_id)
    assert restored is not None
    assert restored.status == "waiting_input"
    assert restored.failure == {
        "category": "user_input",
        "missing": ["serial_port"],
    }


def test_sqlite_agent_session_survives_repository_restart(tmp_path: Path) -> None:
    path = tmp_path / "application.db"
    first = SQLitePersistence(path)
    first.create_agent_session(
        session_id="durable-session",
        project_key="0:test4",
    )
    first.start_agent_turn(
        turn_id="durable-turn",
        session_id="durable-session",
        client_turn_id="browser-1",
        user_message="COM4 好了",
    )

    reopened = SQLitePersistence(path)

    assert reopened.get_agent_session("durable-session") is not None
    assert reopened.get_agent_turn("durable-turn") is not None


def test_tool_execution_ledger_is_idempotent_and_durable(
    repository: TransientPersistence | SQLitePersistence,
) -> None:
    repository.create_agent_session(
        session_id="tool-session",
        project_key="0:test4",
    )
    repository.start_agent_turn(
        turn_id="tool-turn",
        session_id="tool-session",
        client_turn_id="tool-client",
        user_message="烧录",
    )

    reserved, created = repository.reserve_tool_execution(
        idempotency_key="tool-session:tool-turn:flash-1",
        session_id="tool-session",
        turn_id="tool-turn",
        call_id="flash-1",
        tool_name="device.flash",
        arguments_fingerprint='{"serial_port": "COM4"}',
    )
    replay, replay_created = repository.reserve_tool_execution(
        idempotency_key="tool-session:tool-turn:flash-1",
        session_id="tool-session",
        turn_id="tool-turn",
        call_id="flash-1",
        tool_name="device.flash",
        arguments_fingerprint='{"serial_port": "COM4"}',
    )

    assert created is True
    assert replay_created is False
    assert replay == reserved

    completed = repository.finish_tool_execution(
        reserved.idempotency_key,
        status="succeeded",
        result={"flashed": True},
    )
    replay_completed, _ = repository.reserve_tool_execution(
        idempotency_key=reserved.idempotency_key,
        session_id="tool-session",
        turn_id="tool-turn",
        call_id="flash-1",
        tool_name="device.flash",
        arguments_fingerprint='{"serial_port": "COM4"}',
    )

    assert completed.status == "succeeded"
    assert replay_completed.result == {"flashed": True}
