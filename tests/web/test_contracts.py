from pydantic import ValidationError
import pytest

from luxar.web_contracts import (
    WebAgentInteractionRequest,
    WebCancelRequest,
    WebSteeringRequest,
    WebTaskRequest,
)


def test_web_task_request_normalizes_safe_values() -> None:
    request = WebTaskRequest(
        message="  修复 GPIO 2  ",
        max_attempts=5,
        allow_dependency_downloads=True,
    )

    assert request.message == "修复 GPIO 2"
    assert request.max_attempts == 5
    assert request.allow_dependency_downloads is True
    assert request.stream is True


def test_web_task_request_accepts_continuous_agent_identity() -> None:
    request = WebTaskRequest(
        message="继续烧录",
        session_id="session_abc-123",
        client_turn_id="browser:42",
    )

    assert request.session_id == "session_abc-123"
    assert request.client_turn_id == "browser:42"


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "   "},
        {"message": "build", "max_attempts": 0},
        {"message": "build", "max_attempts": 11},
        {"message": "build", "stream": False},
        {"message": "build", "docs": ["secret"]},
        {"message": "build", "allow_dependency_downloads": 1},
        {"message": "build", "session_id": "../other"},
        {"message": "build", "client_turn_id": "has space"},
    ],
)
def test_web_task_request_rejects_invalid_or_legacy_data(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        WebTaskRequest.model_validate(payload)


def test_agent_interaction_request_keeps_intent_separate() -> None:
    request = WebAgentInteractionRequest(
        kind="change_plan",
        message="  先执行 Host 测试  ",
        target_id="task-1",
    )

    assert request.message == "先执行 Host 测试"
    assert request.kind == "change_plan"


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "question", "message": "   "},
        {"kind": "continue", "message": "继续"},
        {"kind": "change_plan", "message": "调整", "approved": True},
    ],
)
def test_agent_interaction_request_rejects_ambiguous_payload(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        WebAgentInteractionRequest.model_validate(payload)


def test_steering_and_cancel_contracts_keep_session_identity_safe() -> None:
    steering = WebSteeringRequest(
        message="  改用 COM4  ",
        client_steering_id="steering:1",
        session_id="session-1",
    )
    cancellation = WebCancelRequest(session_id="session-1")

    assert steering.message == "改用 COM4"
    assert steering.client_steering_id == "steering:1"
    assert cancellation.session_id == "session-1"

    with pytest.raises(ValidationError):
        WebSteeringRequest(message="   ")
    with pytest.raises(ValidationError):
        WebCancelRequest(session_id="../other")
