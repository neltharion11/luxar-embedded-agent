from collections.abc import Callable

import pytest

from luxar.application.continuous_agent_identity import (
    begin_continuous_agent_turn,
)
from luxar.database import TransientPersistence


def _ids() -> Callable[[str], str]:
    counters: dict[str, int] = {}

    def create(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-{counters[prefix]}"

    return create


def test_consecutive_turns_reuse_active_session() -> None:
    persistence = TransientPersistence()
    id_factory = _ids()

    first = begin_continuous_agent_turn(
        persistence,
        project_key="0:test4",
        user_message="重新烧录",
        id_factory=id_factory,
    )
    second = begin_continuous_agent_turn(
        persistence,
        project_key="0:test4",
        user_message="串口好了，是 COM4",
        id_factory=id_factory,
    )

    assert first.session.session_id == second.session.session_id
    assert first.turn.turn_id != second.turn.turn_id
    assert second.turn.user_message == "串口好了，是 COM4"


def test_client_turn_id_is_idempotent() -> None:
    persistence = TransientPersistence()
    id_factory = _ids()

    first = begin_continuous_agent_turn(
        persistence,
        project_key="0:test4",
        user_message="重新烧录",
        client_turn_id="browser-request-1",
        id_factory=id_factory,
    )
    replay = begin_continuous_agent_turn(
        persistence,
        project_key="0:test4",
        user_message="重新烧录",
        requested_session_id=first.session.session_id,
        client_turn_id="browser-request-1",
        id_factory=id_factory,
    )

    assert replay.replayed is True
    assert replay.turn.turn_id == first.turn.turn_id


def test_session_cannot_cross_project_boundary() -> None:
    persistence = TransientPersistence()
    session = persistence.create_agent_session(
        session_id="session-shared",
        project_key="0:test4",
    )

    with pytest.raises(ValueError, match="不属于当前项目"):
        begin_continuous_agent_turn(
            persistence,
            project_key="0:other",
            user_message="继续",
            requested_session_id=session.session_id,
        )


def test_archived_session_rejects_new_turn() -> None:
    persistence = TransientPersistence()
    persistence.create_agent_session(
        session_id="session-old",
        project_key="0:test4",
    )
    assert persistence.archive_agent_session("session-old") is True

    with pytest.raises(ValueError, match="已归档"):
        begin_continuous_agent_turn(
            persistence,
            project_key="0:test4",
            user_message="继续",
            requested_session_id="session-old",
        )
