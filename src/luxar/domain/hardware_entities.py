"""硬件实体：跨文档聚合同一硬件的知识归属模型。

同一硬件可能被多份文档拆分描述（芯片手册定义命令、屏厂手册定义初始化序列），
且硬件分两层：

- chip（芯片类）：控制器/传感器本身（如 SH1106）。所有使用它的硬件实例共享
  其命令定义、寄存器、位序。
- device（硬件实例）：具体模组/产品（如"1.3寸横屏屏模组"）。引用一个 chip，
  自有 init 序列、分辨率、引脚映射、镜像设置——换一块同样用 SH1106 的屏，
  序列可能不同。

知识原子通过 entity_id 归属实体；检索时按实体聚合（device 沿 chip_ref 带上
chip 的知识）。实体建立需要用户确认（agent 只提议候选，不自行合并）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

EntityKind = Literal["chip", "device"]


@dataclass(frozen=True)
class HardwareEntity:
    """一个硬件实体（chip 类或 device 实例）。"""

    entity_id: str
    kind: EntityKind
    name: str
    #: device 引用的 chip 实体 id（device 专有；chip 为 None）
    chip_ref: str | None = None
    #: 描述本实体的来源文档（source_uri 列表，可多份）
    source_uris: tuple[str, ...] = ()
    #: 可检索别名（如 "sh1106"、"1.3寸横屏"）
    aliases: tuple[str, ...] = ()
    #: 自由附加描述（如型号、厂商）
    notes: str = ""

    @property
    def match_names(self) -> set[str]:
        names = {self.name.casefold(), self.entity_id.casefold()}
        names.update(alias.casefold() for alias in self.aliases)
        return names

    def to_row(self) -> dict[str, str]:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind,
            "name": self.name,
            "chip_ref": self.chip_ref or "",
            "source_uris": ",".join(self.source_uris),
            "aliases": ",".join(self.aliases),
            "notes": self.notes,
        }


def entity_id_for(kind: EntityKind, name: str) -> str:
    """确定性实体 id：kind + 规范化名称（稳定、可复现）。"""
    import hashlib

    digest = hashlib.sha256(name.strip().casefold().encode("utf-8")).hexdigest()[:16]
    return f"{kind}-{digest}"


__all__ = ["HardwareEntity", "entity_id_for"]
