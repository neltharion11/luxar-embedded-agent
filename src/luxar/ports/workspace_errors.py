"""工作区错误合同：把文件系统异常统一成应用能够理解的稳定失败语义。"""

from __future__ import annotations

from typing import Literal


WorkspaceErrorCategory = Literal[
    "invalid_project",
    "unsafe_path",
    "unsupported_file",
    "file_too_large",
    "context_too_large",
    "invalid_encoding",
    "io",
    "rollback_failed",
]


class WorkspaceError(RuntimeError):
    def __init__(
        self,
        *,
        category: WorkspaceErrorCategory,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)

        self.category = category
        self.message = message
        self.retryable = retryable