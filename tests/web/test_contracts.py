from pydantic import ValidationError
import pytest

from luxar.web_contracts import WebTaskRequest


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


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "   "},
        {"message": "build", "max_attempts": 0},
        {"message": "build", "max_attempts": 11},
        {"message": "build", "stream": False},
        {"message": "build", "docs": ["secret"]},
        {"message": "build", "allow_dependency_downloads": 1},
    ],
)
def test_web_task_request_rejects_invalid_or_legacy_data(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        WebTaskRequest.model_validate(payload)
