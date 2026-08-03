import pytest

from luxar.ports.errors import CapabilityError, CapabilityErrorCategory


@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        ("authentication", False),
        ("timeout", True),
        ("rate_limit", True),
        ("service", True),
        ("empty_response", True),
        ("invalid_json", True),
        ("invalid_schema", False),
    ],
)
def test_capability_error_preserves_stable_failure_facts(
    category: CapabilityErrorCategory,
    retryable: bool,
) -> None:
    error = CapabilityError(
        category=category,
        message="sanitized capability failure",
        retryable=retryable,
    )

    assert error.category == category
    assert error.message == "sanitized capability failure"
    assert error.retryable is retryable
    assert str(error) == "sanitized capability failure"
    assert isinstance(error, RuntimeError)
