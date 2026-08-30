"""Semantic knowledge units indexed independently from document pagination."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class KnowledgeAtomDraft(BaseModel):
    """One self-contained fact extracted from an untrusted source document."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=240)
    statement: str = Field(min_length=1, max_length=4000)
    category: str = Field(default="general", min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list)
    applicable_conditions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list, max_length=30)
    source_section: str | None = Field(default=None, max_length=300)
    source_excerpt: str = Field(default="", max_length=4000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator(
        "subject",
        "statement",
        "category",
        "source_section",
        "source_excerpt",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("aliases", "applicable_conditions", "limitations")
    @classmethod
    def _clean_text_list(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        # 模型会把"COM0-COM63"这类枚举全量展开进 aliases（远超市面惯例），
        # 截断而非报错：保留前 30 项足以作为检索别名。
        return result[:30]

    @field_validator("source_pages")
    @classmethod
    def _clean_pages(cls, values: list[int]) -> list[int]:
        return sorted({value for value in values if value > 0})


class ParameterAtomDraft(BaseModel):
    """One device parameter fact extracted verbatim from a source document.

    与散文型知识原子（KnowledgeAtomDraft）并列的参数型原子：面向代码生成的结构化
    事实（初始化序列、寄存器地址、引脚映射、时序/尺寸参数等）。value 是结构化值，
    消费方可直接转写为数据表，无需"散文→代码"的再解读（再解读是 oled10 init 抄错
    的根因）。

    硬性约束：source_excerpt 必须非空，且入库前经机械校验为原文字串
    （见 DeepSeekKnowledgeAtomExtractor._verbatim_check）——摘录而非回忆。
    """

    model_config = ConfigDict(extra="forbid")

    parameter: str = Field(min_length=1, max_length=160)
    value_type: Literal["bytes", "sequence", "int", "float", "text", "enum"] = "text"
    value: str = Field(min_length=1, max_length=8000)
    scope: dict[str, str] = Field(
        default_factory=dict,
        description="芯片/设备实体锚定，如 {controller: sh1106, interface: i2c}",
    )
    unit: str = Field(default="", max_length=40)
    source_pages: list[int] = Field(default_factory=list, max_length=30)
    source_section: str | None = Field(default=None, max_length=300)
    source_excerpt: str = Field(min_length=1, max_length=8000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("parameter", "source_section", "source_excerpt", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("value", mode="before")
    @classmethod
    def _value_to_text(cls, value: object) -> object:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            # sequence 型：模型输出命令列表，序列化为有序 JSON 字符串
            return json.dumps(value, ensure_ascii=False)
        return value

    @field_validator("value")
    @classmethod
    def _sequence_must_be_list(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("value_type") != "sequence":
            return value
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("sequence 型参数 value 必须是 JSON 命令列表") from error
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("sequence 型参数 value 必须是非空命令列表")
        for index, item in enumerate(parsed):
            if not isinstance(item, dict) or "cmd" not in item:
                raise ValueError(f"sequence 第 {index} 项必须是 {{cmd: ...}}")
            cmd = item["cmd"]
            if not (0 <= int(cmd) <= 0xFF):
                raise ValueError(f"sequence 第 {index} 项 cmd 越界：{cmd}")
            args = item.get("args", [])
            if isinstance(args, list) and any(
                not (0 <= int(arg) <= 0xFF) for arg in args
            ):
                raise ValueError(f"sequence 第 {index} 项 args 越界")
        return value

    @field_validator("scope", mode="before")
    @classmethod
    def _clean_scope(cls, values: object) -> dict[str, str]:
        if not isinstance(values, dict):
            return {}
        cleaned: dict[str, str] = {}
        for key, val in values.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            # 模型可能输出 bool/int 值（如 reset: true），统一转为字符串；
            # 布尔按 json 风格小写（true/false）保留语义。
            if isinstance(val, bool):
                val_text = "true" if val else "false"
            else:
                val_text = str(val).strip()
            if val_text:
                cleaned[key_text.casefold()] = val_text
        return cleaned

    @field_validator("source_pages")
    @classmethod
    def _clean_pages(cls, values: list[int]) -> list[int]:
        return sorted({value for value in values if value > 0})


class KnowledgeAtomExtraction(BaseModel):
    """Structured response contract used by model-backed extractors."""

    model_config = ConfigDict(extra="forbid")

    atoms: list[KnowledgeAtomDraft] = Field(default_factory=list, max_length=300)
    parameters: list[ParameterAtomDraft] = Field(default_factory=list, max_length=200)


@dataclass(frozen=True)
class KnowledgeAtom:
    """Validated knowledge persisted as the searchable unit."""

    knowledge_id: str
    subject: str
    statement: str
    category: str
    aliases: tuple[str, ...] = ()
    applicable_conditions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    source_document_id: str = ""
    source_uri: str = ""
    source_title: str = ""
    source_pages: tuple[int, ...] = ()
    source_section: str | None = None
    source_excerpt: str = ""
    confidence: float = 1.0
    #: 参数型原子专用（category="parameter"）：结构化值/单位/设备实体锚定。
    #: 消费方可直接把 value 转写为数据表，无需"散文→代码"再解读。
    parameter_value: str = ""
    parameter_unit: str = ""
    parameter_scope: dict[str, str] | None = None
    #: 硬件实体归属（chip/device 的 entity_id）；未关联时为空。
    entity_id: str = ""

    def searchable_text(self) -> str:
        """Build the embedding text from knowledge, never from a page label."""

        parts = [f"主题：{self.subject}", f"知识：{self.statement}"]
        if self.category:
            parts.append(f"类别：{self.category}")
        if self.aliases:
            parts.append("别名：" + "、".join(self.aliases))
        if self.applicable_conditions:
            parts.append("适用条件：" + "；".join(self.applicable_conditions))
        if self.limitations:
            parts.append("限制：" + "；".join(self.limitations))
        return "\n".join(parts)

    def metadata(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": 2,
            "unit_type": "knowledge_atom",
            "knowledge_id": self.knowledge_id,
            "subject": self.subject,
            "category": self.category,
            "aliases": list(self.aliases),
            "applicable_conditions": list(self.applicable_conditions),
            "limitations": list(self.limitations),
            "source_document_id": self.source_document_id,
            "source_pages": list(self.source_pages),
            "source_section": self.source_section,
            "source_excerpt": self.source_excerpt,
            "confidence": self.confidence,
        }
        if self.category == "parameter":
            result["parameter_value"] = self.parameter_value
            result["parameter_unit"] = self.parameter_unit
            result["parameter_scope"] = dict(self.parameter_scope or {})
        if self.entity_id:
            result["entity_id"] = self.entity_id
        return result


@dataclass(frozen=True)
class KnowledgeChunk:
    """Storage-neutral indexed record used by knowledge backends."""

    content: str
    token_count: int
    embedding: list[float]
    metadata: dict[str, object] = field(default_factory=dict)


_SPACE_RE = re.compile(r"\s+")


def _identity_text(draft: KnowledgeAtomDraft) -> str:
    return "|".join(
        [
            _SPACE_RE.sub(" ", draft.subject).strip().casefold(),
            _SPACE_RE.sub(" ", draft.statement).strip().casefold(),
            "|".join(value.casefold() for value in draft.applicable_conditions),
            "|".join(value.casefold() for value in draft.limitations),
        ]
    )


def materialize_knowledge_atoms(
    drafts: Sequence[KnowledgeAtomDraft],
    *,
    document_id: str,
    source_uri: str,
    source_title: str,
) -> list[KnowledgeAtom]:
    """Validate, deduplicate and attach provenance to extracted facts."""

    atoms: list[KnowledgeAtom] = []
    seen: set[str] = set()
    for draft in drafts:
        identity = _identity_text(draft)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        atoms.append(
            KnowledgeAtom(
                knowledge_id=f"ka-{digest[:24]}",
                subject=draft.subject,
                statement=draft.statement,
                category=draft.category,
                aliases=tuple(draft.aliases),
                applicable_conditions=tuple(draft.applicable_conditions),
                limitations=tuple(draft.limitations),
                source_document_id=document_id,
                source_uri=source_uri,
                source_title=source_title,
                source_pages=tuple(draft.source_pages),
                source_section=draft.source_section,
                source_excerpt=draft.source_excerpt or draft.statement,
                confidence=draft.confidence,
            )
        )
    return atoms


def materialize_parameter_atoms(
    drafts: Sequence[ParameterAtomDraft],
    *,
    document_id: str,
    source_uri: str,
    source_title: str,
) -> list[KnowledgeAtom]:
    """把参数型原子固化为可检索单元（metadata 携带结构化 value/scope）。

    searchable_text 含 parameter 名 + scope 键值 + value 原文，使召回既能按
    "sh1106 init" 语义命中，也能按 value 中的十六进制标识符词法命中。
    """
    atoms: list[KnowledgeAtom] = []
    seen: set[str] = set()
    for draft in drafts:
        identity = "|".join(
            [
                _SPACE_RE.sub(" ", draft.parameter).strip().casefold(),
                _SPACE_RE.sub(" ", draft.value).strip().casefold(),
                "|".join(
                    f"{key}={value.casefold()}" for key, value in sorted(draft.scope.items())
                ),
            ]
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        scope_text = "；".join(f"{k}={v}" for k, v in sorted(draft.scope.items()))
        value_text = draft.value
        if draft.value_type == "sequence":
            # sequence 值展开为"命令序列"可检索文本（每个命令字节可词法命中）
            try:
                commands = json.loads(draft.value)
                parts = []
                for item in commands:
                    cmd = f"0x{int(item['cmd']):02X}"
                    args = item.get("args") or []
                    if args:
                        cmd += " " + " ".join(f"0x{int(a):02X}" for a in args)
                    parts.append(cmd)
                if parts:
                    value_text = "序列： " + " → ".join(parts) + "；原始：" + draft.value
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass  # 保持原样
        searchable = (
            f"参数：{draft.parameter}；范围：{scope_text}；值：{value_text}"
            + (f"；单位：{draft.unit}" if draft.unit else "")
            + f"；原文：{draft.source_excerpt}"
        )
        atoms.append(
            KnowledgeAtom(
                knowledge_id=f"pa-{digest[:24]}",
                subject=draft.parameter,
                statement=searchable,
                category="parameter",
                source_document_id=document_id,
                source_uri=source_uri,
                source_title=source_title,
                source_pages=tuple(draft.source_pages),
                source_section=draft.source_section,
                source_excerpt=draft.source_excerpt,
                confidence=draft.confidence,
                parameter_value=draft.value,
                parameter_unit=draft.unit,
                parameter_scope=dict(draft.scope),
            )
        )
    return atoms


__all__ = [
    "KnowledgeAtom",
    "KnowledgeAtomDraft",
    "KnowledgeAtomExtraction",
    "KnowledgeChunk",
    "ParameterAtomDraft",
    "materialize_knowledge_atoms",
    "materialize_parameter_atoms",
]
