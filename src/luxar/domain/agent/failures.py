"""Agent 任务失败记录、错误签名和有限重试策略。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.agent.tasks import AgentTask


FailureCategory = Literal["schema", "semantic", "execution"]
FailureStatus = Literal["pending", "blocked"]


class AgentFailureRecord(BaseModel):
    """可 checkpoint 的结构化失败事实，不保存原始模型输入。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: str = Field(min_length=1, max_length=240)
    category: FailureCategory
    signature: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=2000)
    errors: list[dict[str, object]] = Field(default_factory=list, max_length=40)
    attempt: int = Field(ge=1)
    repeated: bool = False


def failure_signature(
    task_id: str,
    category: FailureCategory,
    details: Iterable[str] = (),
) -> str:
    """为同一任务的同一类语义错误生成稳定、短且不泄露内容的签名。"""

    material = "\x1f".join(
        [task_id.strip(), category, *(detail.strip() for detail in details)]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"{category}:{digest}"


def decide_failure_status(
    task: AgentTask,
    history: Sequence[AgentFailureRecord],
    signature: str,
) -> tuple[FailureStatus, int, bool]:
    """返回下一状态、下一尝试次数和是否重复错误。"""

    attempt = task.attempts + 1
    repeated = any(
        item.task_id == task.task_id and item.signature == signature
        for item in history
    )
    status: FailureStatus = (
        "blocked"
        if repeated or attempt >= task.max_attempts
        else "pending"
    )
    return status, attempt, repeated


__all__ = [
    "AgentFailureRecord",
    "FailureCategory",
    "FailureStatus",
    "decide_failure_status",
    "failure_signature",
]
