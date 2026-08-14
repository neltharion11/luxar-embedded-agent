import pytest

from luxar.ports.espidf_errors import (
    EspIdfError,
    EspIdfErrorCategory,
)


@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        ("invalid_project", False),
        ("environment", False),
        ("dependency", False),
        ("process", True),
    ],
)
def test_espidf_error_preserves_stable_failure_facts(
    category: EspIdfErrorCategory,
    retryable: bool,
) -> None:
    error = EspIdfError(
        category=category,
        message="stable message",
        retryable=retryable,
    )

    assert str(error) == "stable message"
    assert error.category == category
    assert error.message == "stable message"
    assert error.retryable is retryable
