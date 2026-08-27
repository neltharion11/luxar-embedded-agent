"""模型结构化输出的一次性 Schema 自动修复边界。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError


ModelT = TypeVar("ModelT", bound=BaseModel)


def _safe_validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    """只保留修复所需的字段位置和消息，不保存原始 input/ctx。"""

    return [
        {
            "type": item.get("type", "validation_error"),
            "loc": list(item.get("loc", [])),
            "msg": item.get("msg", "字段校验失败"),
        }
        for item in error.errors()
    ]


class SchemaRepairExhausted(ValueError):
    """原始输出和一次修复输出均未通过领域 Schema。"""

    def __init__(self, model_name: str, errors: list[dict[str, Any]]) -> None:
        self.model_name = model_name
        self.errors = errors
        super().__init__(f"schema repair exhausted for {model_name}")


def validate_with_one_repair(
    model: type[ModelT],
    payload: object,
    repair: Callable[[object, list[dict[str, Any]]], object] | None = None,
) -> ModelT:
    """验证模型输出；失败时把精确错误交给修复器，并最多再验证一次。"""

    try:
        return model.model_validate(payload)
    except ValidationError as first_error:
        safe_errors = _safe_validation_errors(first_error)
        if repair is None:
            raise SchemaRepairExhausted(model.__name__, safe_errors) from first_error
        repaired_payload = repair(payload, safe_errors)
        try:
            return model.model_validate(repaired_payload)
        except ValidationError as second_error:
            raise SchemaRepairExhausted(
                model.__name__, _safe_validation_errors(second_error)
            ) from second_error
