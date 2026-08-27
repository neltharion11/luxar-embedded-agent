"""Deterministic semantic knowledge extraction for offline operation."""

from __future__ import annotations

import re
from collections.abc import Sequence

from luxar.document_reader import PdfBatch
from luxar.domain.knowledge_atoms import KnowledgeAtomDraft


_PAGE_MARKER_RE = re.compile(r"(?m)^## 第\s*(\d+)\s*页\s*$")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")
_SENTENCE_RE = re.compile(r"(?<=[。！？；.!?;])\s+|\n{2,}")
_TECHNICAL_TOKEN_RE = re.compile(
    r"(?:GPIO|IO\d+|ADC|DAC|UART|I2C|I²C|SPI|PWM|MHz|kHz|mA|V\b|"
    r"引脚|电压|电流|地址|寄存器|输入|输出|上拉|下拉|复位|启动|时序)",
    re.IGNORECASE,
)


def _category(text: str) -> str:
    lowered = text.casefold()
    categories = (
        (("gpio", "引脚", "输入", "输出", "上拉", "下拉"), "pin"),
        (("adc", "dac", "模拟"), "analog"),
        (("uart", "i2c", "i²c", "spi", "通信", "总线"), "communication"),
        (("电压", "电流", "供电", "vdd", "gnd"), "electrical"),
        (("启动", "boot", "strapping", "复位"), "boot"),
        (("寄存器", "命令", "位定义"), "register"),
        (("限制", "禁止", "不能", "仅", "注意"), "constraint"),
    )
    for keywords, name in categories:
        if any(keyword in lowered for keyword in keywords):
            return name
    return "general"


def _subject(statement: str, section: str | None) -> str:
    if section:
        return section[:240]
    prefix = re.split(r"[：:，,。；;]", statement, maxsplit=1)[0].strip()
    return (prefix or statement)[:240]


def _page_sections(batch: PdfBatch) -> list[tuple[int, str]]:
    matches = list(_PAGE_MARKER_RE.finditer(batch.content))
    if not matches:
        return [(batch.start_page, batch.content)]
    sections: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(batch.content)
        sections.append((int(match.group(1)), batch.content[match.end() : end].strip()))
    return sections


class SemanticKnowledgeAtomExtractor:
    """Extract paragraph/table-row facts without treating pages as knowledge."""

    def extract(
        self,
        *,
        title: str,
        source_uri: str,
        batches: Sequence[PdfBatch],
    ) -> list[KnowledgeAtomDraft]:
        del title, source_uri
        drafts: list[KnowledgeAtomDraft] = []
        for batch in batches:
            for page, content in _page_sections(batch):
                current_section: str | None = (
                    " / ".join(batch.section_path)
                    if batch.section_path
                    else batch.section_title or None
                )
                buffer: list[str] = []

                def flush() -> None:
                    if not buffer:
                        return
                    paragraph = " ".join(buffer).strip()
                    buffer.clear()
                    for raw in _SENTENCE_RE.split(paragraph):
                        statement = re.sub(r"\s+", " ", raw).strip(" -\t")
                        if len(statement) < 12 and not _TECHNICAL_TOKEN_RE.search(statement):
                            continue
                        drafts.append(
                            KnowledgeAtomDraft(
                                subject=_subject(statement, current_section),
                                statement=statement[:4000],
                                category=_category(statement),
                                source_pages=[page],
                                source_section=current_section,
                                source_excerpt=statement[:4000],
                                confidence=0.75,
                            )
                        )

                for raw_line in content.splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("[页面视觉元素："):
                        flush()
                        continue
                    heading = _HEADING_RE.match(line)
                    if heading:
                        flush()
                        current_section = heading.group(1).strip()[:300]
                        continue
                    # Table rows and list entries are already independent facts.
                    if "|" in line or re.match(r"^(?:[-*•]|\d+[.)、])\s+", line):
                        flush()
                        buffer.append(line)
                        flush()
                    else:
                        buffer.append(line)
                flush()
        return drafts


__all__ = ["SemanticKnowledgeAtomExtractor"]
