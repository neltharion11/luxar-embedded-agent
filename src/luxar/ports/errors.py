"""能力错误合同：把不同 SDK 的异常统一成应用能够理解的稳定失败语义。"""

from __future__ import annotations

from typing import Literal


CapabilityErrorCategory = Literal[
    # 这些类别与具体供应商异常类解耦，Application 可以据此决定是否可恢复。
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
        # 初始化标准异常部分，使 str(error) 和正常的 raise/except 行为可用。
        super().__init__(message)

        self.category = category
        self.message = message
        self.retryable = retryable
