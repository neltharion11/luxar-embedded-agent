"""Resolve stable Agent Sessions and idempotent per-message Turns."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from luxar.database.persistence import (
    AgentSessionRecord,
    AgentTurnRecord,
    PersistencePort,
)


@dataclass(frozen=True)
class ContinuousAgentTurnIdentity:
    session: AgentSessionRecord
    turn: AgentTurnRecord
    replayed: bool


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def begin_continuous_agent_turn(
    persistence: PersistencePort,
    *,
    project_key: str,
    user_message: str,
    requested_session_id: str | None = None,
    client_turn_id: str | None = None,
    id_factory: Callable[[str], str] = _new_id,
) -> ContinuousAgentTurnIdentity:
    """Get/create a Session, then idempotently create its next Turn."""

    if not project_key.strip() or not user_message.strip():
        raise ValueError("项目标识和用户消息不能为空")

    session: AgentSessionRecord | None
    if requested_session_id is not None:
        session = persistence.get_agent_session(requested_session_id)
        if session is None:
            session = persistence.create_agent_session(
                session_id=requested_session_id,
                project_key=project_key,
            )
    else:
        session = persistence.get_active_agent_session(project_key)
        if session is None:
            session = persistence.create_agent_session(
                session_id=id_factory("session"),
                project_key=project_key,
            )

    if session.project_key != project_key:
        raise ValueError("Agent Session 不属于当前项目")
    if session.status != "active":
        raise ValueError("Agent Session 已归档")

    resolved_client_turn_id = client_turn_id or id_factory("client")
    existing = persistence.get_agent_turn_by_client_id(
        session_id=session.session_id,
        client_turn_id=resolved_client_turn_id,
    )
    if existing is not None:
        return ContinuousAgentTurnIdentity(
            session=session,
            turn=existing,
            replayed=True,
        )

    turn = persistence.start_agent_turn(
        turn_id=id_factory("turn"),
        session_id=session.session_id,
        client_turn_id=resolved_client_turn_id,
        user_message=user_message,
    )
    return ContinuousAgentTurnIdentity(
        session=session,
        turn=turn,
        replayed=False,
    )


__all__ = ["ContinuousAgentTurnIdentity", "begin_continuous_agent_turn"]
