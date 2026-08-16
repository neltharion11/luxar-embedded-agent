"""ESP-IDF Port 异常：描述命令开始前的稳定、可脱敏能力失败。"""

from __future__ import annotations

from typing import Literal


EspIdfErrorCategory = Literal[
    "invalid_project",
    "environment",
    "dependency",
    "process",
    "serial",
]


class EspIdfError(RuntimeError):
    def __init__(
        self,
        *,
        category: EspIdfErrorCategory,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.retryable = retryable