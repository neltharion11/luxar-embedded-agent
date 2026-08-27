"""Durable steering queue built on the append-only Agent interaction log."""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass

from luxar.database.persistence import PersistencePort


@dataclass(frozen=True)
class SteeringMessage:
    steering_id: str
    message: str


class ContinuousAgentSteeringQueue:
    def __init__(
        self,
        persistence: PersistencePort,
        *,
        project_key: str,
        session_id: str,
    ) -> None:
        self._persistence = persistence
        self._project_key = project_key
        self._session_id = session_id

    def enqueue(
        self,
        message: str,
        *,
        client_steering_id: str | None = None,
    ) -> SteeringMessage:
        normalized = message.strip()
        if not normalized:
            raise ValueError("steering 消息不能为空")
        steering_id = client_steering_id or uuid.uuid4().hex
        interaction_id = f"steering:{self._session_id}:{steering_id}"
        self._persistence.append_agent_interaction(
            interaction_id=interaction_id,
            project_key=self._project_key,
            objective_id=None,
            kind="continuous_agent_steering",
            payload={
                "session_id": self._session_id,
                "steering_id": steering_id,
                "message": normalized,
                "queued_at_ns": time.time_ns(),
            },
        )
        return SteeringMessage(steering_id=steering_id, message=normalized)

    def drain(self) -> list[SteeringMessage]:
        interactions = self._persistence.get_agent_interactions(
            self._project_key,
            limit=500,
        )
        consumed = {
            str(record.payload.get("steering_id"))
            for record in interactions
            if record.kind == "continuous_agent_steering_consumed"
            and record.payload.get("session_id") == self._session_id
        }
        pending_records: list[tuple[int, str, SteeringMessage]] = []
        for record in interactions:
            if (
                record.kind != "continuous_agent_steering"
                or record.payload.get("session_id") != self._session_id
            ):
                continue
            steering_id = str(record.payload.get("steering_id", ""))
            message = str(record.payload.get("message", "")).strip()
            if not steering_id or not message or steering_id in consumed:
                continue
            pending_records.append(
                (
                    int(record.payload.get("queued_at_ns", 0)),
                    record.interaction_id,
                    SteeringMessage(steering_id=steering_id, message=message),
                )
            )
        pending = [
            item
            for _, _, item in sorted(
                pending_records,
                key=lambda record: (record[0], record[1]),
            )
        ]
        for item in pending:
            self._persistence.append_agent_interaction(
                interaction_id=(
                    f"steering-consumed:{self._session_id}:{item.steering_id}"
                ),
                project_key=self._project_key,
                objective_id=None,
                kind="continuous_agent_steering_consumed",
                payload={
                    "session_id": self._session_id,
                    "steering_id": item.steering_id,
                },
            )
        return pending


__all__ = ["ContinuousAgentSteeringQueue", "SteeringMessage"]
