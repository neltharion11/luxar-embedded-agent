"""引用式驱动校验：代码里的硬件字节必须能在手册原文中找到出处。

背景：模型写驱动代码的不可靠点在"硬件事实"（init 字节、寄存器地址、引脚值），
而不是代码组织。解决方式不是预置骨架（枚举设备类别永远追不上），而是**引用式
校验**——模型自由组织代码结构，但每个硬件字节必须引用 RAG 检索到的参数原子，
且能在该原子的 source_excerpt（手册原文）中逐字定位。

规则（与设备无关，只有两条）：
1. 代码中出现的十六进制字节 ⊆ 引用原子的 source_excerpt 字节集合；
2. 引用原子必须来自手册（带 entity_id / source_excerpt），不是模型记忆。

check_driver() 返回逐条违规（字节 + 代码行号 + 缺失出处），供工具层报错。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: C 代码中的十六进制字面量：0xAE / 0xAEU / 0xAEul 等
_HEX_LITERAL_RE = re.compile(
    r"0[xX]([0-9A-Fa-f]+)(?![0-9A-Fa-fxX])"
)
#: 手册原文中的字节记法：0xAE、AEH、0AEH 等
_EXCERPT_HEX_RE = re.compile(r"0[xX]([0-9A-Fa-f]{1,4})")
_EXCERPT_SUFFIX_RE = re.compile(r"(?<![0-9A-Fa-fxX])([0-9A-Fa-f]{1,4})[hH](?![0-9A-Fa-f])")


@dataclass(frozen=True)
class ReferencedAtom:
    """模型声明引用的一个知识原子。"""

    knowledge_id: str
    #: 手册原文摘录（source_excerpt），必须逐字来自手册
    excerpt: str
    #: 原子归属的硬件实体（chip/device entity_id）；空 = 未归属，不可作依据
    entity_id: str = ""
    subject: str = ""


@dataclass(frozen=True)
class DriverViolation:
    """一条引用违规：代码字节在引用的手册原文中找不到出处。"""

    line: int
    code: str
    byte_value: int
    #: 这个字节本应引用的设备（提示信息用）
    hint: str = ""


@dataclass(frozen=True)
class DriverCheckResult:
    """一次校验的完整结果。"""

    violations: list[DriverViolation] = field(default_factory=list)
    #: 代码中出现的全部字节（去重排序）
    code_bytes: tuple[int, ...] = ()
    #: 引用原子提供的全部字节（去重排序）
    referenced_bytes: tuple[int, ...] = ()
    #: 无 entity_id 的引用原子（可能是模型记忆，不是手册）
    unattributed_refs: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations and not self.unattributed_refs


def _extract_code_bytes(code: str) -> dict[int, list[int]]:
    """提取 C 代码中所有十六进制字面量 -> 出现的行号列表。"""
    found: dict[int, list[int]] = {}
    for line_number, line in enumerate(code.splitlines(), start=1):
        for match in _HEX_LITERAL_RE.finditer(line):
            value = int(match.group(1), 16)
            if value > 0xFF:
                continue  # 只关心字节（寄存器值/命令字节）
            found.setdefault(value, []).append(line_number)
    return found


def _extract_excerpt_bytes(excerpt: str) -> set[int]:
    """提取手册原文中的字节（0xAE 与 AEH 两种记法）。"""
    result: set[int] = set()
    for match in _EXCERPT_HEX_RE.finditer(excerpt):
        value = int(match.group(1), 16)
        if value <= 0xFF:
            result.add(value)
    for match in _EXCERPT_SUFFIX_RE.finditer(excerpt):
        value = int(match.group(1), 16)
        if value <= 0xFF:
            result.add(value)
    return result


def check_driver(
    code: str,
    references: list[ReferencedAtom],
) -> DriverCheckResult:
    """校验驱动代码：每个硬件字节必须能在引用原子的手册原文中找到出处。

    不检查代码逻辑（那是模型擅长且 display.verify/构建能验证的）；
    只检查"硬件事实是否有手册依据"——模型记忆不可靠的部分。
    """
    code_bytes = _extract_code_bytes(code)
    referenced: set[int] = set()
    unattributed: list[str] = []
    for ref in references:
        if not ref.entity_id:
            unattributed.append(ref.subject or ref.knowledge_id)
        referenced |= _extract_excerpt_bytes(ref.excerpt)

    violations: list[DriverViolation] = []
    for value in sorted(code_bytes):
        if value not in referenced:
            for line in code_bytes[value][:3]:
                violations.append(
                    DriverViolation(
                        line=line,
                        code=f"0x{value:02X}",
                        byte_value=value,
                        hint=(
                            f"0x{value:02X} 不在引用的手册原文中"
                        ),
                    )
                )
    return DriverCheckResult(
        violations=violations,
        code_bytes=tuple(sorted(code_bytes)),
        referenced_bytes=tuple(sorted(referenced)),
        unattributed_refs=tuple(unattributed),
    )


__all__ = [
    "DriverCheckResult",
    "DriverViolation",
    "ReferencedAtom",
    "check_driver",
]
