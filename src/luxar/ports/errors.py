from __future__ import annotations

from typing import Literal


CapabilityErrorCategory = Literal[
    "authentication",
    "timeout",
    "rate_limit",
    "service",
    "empty_response",
    "invalid_json",
    "invalid_schema",
]


class CapabilityError(RuntimeError):
    def __init__(
        self,
        *,
        category: CapabilityErrorCategory,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)

        self.category = category
        self.message = message
        self.retryable = retryable