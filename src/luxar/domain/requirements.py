"""固件需求领域模型：把自然语言需求转换成可验证、可路由的结构化数据。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FirmwareRequirement(BaseModel):
    # Literal 限定当前阶段只支持 ESP-IDF，模型返回其他平台时 Pydantic 会拒绝。
    platform: Literal["espidf"] = "espidf"
    target: str
    feature: str
    gpio: int | None = None
    # default_factory 每次都会创建新列表，避免多个需求对象共享同一个可变列表。
    missing_fields: list[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        # @property 让调用方像读取普通字段一样使用 requirement.is_complete。
        # 空列表在布尔判断中为 False，因此 not [] 得到 True，表示需求完整。
        return not self.missing_fields
