from __future__ import annotations

from luxar.application.continuous_agent_steering import (
    ContinuousAgentSteeringQueue,
)
from luxar.database import TransientPersistence


def test_steering_queue_is_ordered_idempotent_and_consumed_once() -> None:
    persistence = TransientPersistence()
    queue = ContinuousAgentSteeringQueue(
        persistence,
        project_key="0:test4",
        session_id="session-1",
    )

    queue.enqueue("改用 COM4", client_steering_id="steer-1")
    queue.enqueue("然后读取串口", client_steering_id="steer-2")
    queue.enqueue("不会覆盖原消息", client_steering_id="steer-1")

    assert [(item.steering_id, item.message) for item in queue.drain()] == [
        ("steer-1", "改用 COM4"),
        ("steer-2", "然后读取串口"),
    ]
    assert queue.drain() == []


def test_steering_queue_isolated_by_session() -> None:
    persistence = TransientPersistence()
    first = ContinuousAgentSteeringQueue(
        persistence,
        project_key="0:test4",
        session_id="session-1",
    )
    second = ContinuousAgentSteeringQueue(
        persistence,
        project_key="0:test4",
        session_id="session-2",
    )

    first.enqueue("第一会话", client_steering_id="same-client-id")
    second.enqueue("第二会话", client_steering_id="same-client-id")

    assert [item.message for item in first.drain()] == ["第一会话"]
    assert [item.message for item in second.drain()] == ["第二会话"]
