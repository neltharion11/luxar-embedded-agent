from __future__ import annotations

import json

import pytest

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.knowledge_answerer import DeepSeekKnowledgeAnswerer
from luxar.adapters.deepseek.knowledge_extractor import DeepSeekKnowledgeAtomExtractor
from luxar.document_reader import PdfBatch
from luxar.domain.knowledge_answers import KnowledgeEvidence
from luxar.ports.errors import CapabilityError


def test_model_extractor_builds_self_contained_knowledge_atoms() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "atoms": [
                    {
                        "subject": "GPIO34",
                        "statement": "GPIO34 只能作为输入使用。",
                        "category": "pin",
                        "aliases": ["IO34"],
                        "applicable_conditions": ["ESP32"],
                        "limitations": ["不能配置为输出"],
                        "source_pages": [8],
                        "source_excerpt": "GPIO34 Input only",
                        "confidence": 0.99,
                    }
                ]
            }
        ]
    )
    extractor = DeepSeekKnowledgeAtomExtractor(client, "fast-test")

    extraction = extractor.extract(
        title="ESP32 数据手册",
        source_uri="docs/esp32.pdf",
        batches=[PdfBatch(
            8,
            8,
            40,
            "## 第 8 页\nGPIO34 Input only",
            False,
            section_title="引脚定义",
            section_path=("硬件接口", "引脚定义"),
            section_level=2,
        )],
    )

    atoms = extraction.atoms
    assert atoms[0].subject == "GPIO34"
    assert atoms[0].limitations == ["不能配置为输出"]
    assert atoms[0].source_section == "硬件接口 / 引脚定义"
    system_prompt, user_prompt, model = client.calls[0]
    assert "页码列表" in system_prompt
    assert "参数型知识原子" in system_prompt  # 新提示词含参数型抽取指引
    assert json.loads(user_prompt)["source_page_range"] == {"start": 8, "end": 8}
    assert json.loads(user_prompt)["source_section_path"] == ["硬件接口", "引脚定义"]
    assert "输入已经按章节组织" in system_prompt
    assert model == "fast-test"


def test_model_extractor_rejects_invented_source_page() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "atoms": [
                    {
                        "subject": "GPIO34",
                        "statement": "GPIO34 只能输入。",
                        "source_pages": [99],
                    }
                ]
            }
        ]
    )

    with pytest.raises(CapabilityError, match="来源页码越出"):
        DeepSeekKnowledgeAtomExtractor(client, "fast-test").extract(
            title="ESP32",
            source_uri="esp32.pdf",
            batches=[PdfBatch(1, 2, 2, "GPIO34 input only", False)],
        )


def test_model_answerer_receives_only_bounded_evidence_contract() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "answer_markdown": "GPIO34 只能作为输入使用。[E1]",
                "cited_evidence_ids": ["E1"],
                "coverage_summary": "覆盖输入限制。",
                "uncertainties": [],
            }
        ]
    )
    answerer = DeepSeekKnowledgeAnswerer(client, "quality-test")
    answer = answerer.answer(
        question="GPIO34 有什么限制？",
        evidence=[
            KnowledgeEvidence(
                evidence_id="E1",
                knowledge_id="ka-1",
                document_id="doc-1",
                title="ESP32 数据手册",
                source_uri="docs/esp32.pdf",
                subject="GPIO34",
                statement="GPIO34 只能作为输入使用。",
                category="pin",
                source_pages=[8],
                score=0.98,
            )
        ],
    )

    assert answer.cited_evidence_ids == ["E1"]
    _, user_prompt, model = client.calls[0]
    supplied = json.loads(user_prompt)
    assert supplied["evidence"][0]["statement"] == "GPIO34 只能作为输入使用。"
    assert "extracted_content" not in supplied


# ---------------------------------------------------------------------------
# 参数型原子（面向代码生成的结构化知识）
# ---------------------------------------------------------------------------


def _sh1106_batch(content: str) -> PdfBatch:
    return PdfBatch(
        12,
        13,
        len(content),
        content,
        False,
        section_title="初始化",
        section_path=("命令表", "初始化"),
        section_level=2,
    )


def test_extractor_builds_parameter_atoms_with_verbatim_excerpt() -> None:
    window = (
        "## 第 12 页\nSH1106 Initialization: 0xAE 0xD5 0x80 0xA8 0x3F "
        "0x40 0xDA 0x12 0xAF"
    )
    client = FakeJsonCompletionClient(
        [
            {
                "atoms": [],
                "parameters": [
                    {
                        "parameter": "init_sequence",
                        "value_type": "bytes",
                        "value": "0xAE 0xD5 0x80 0xA8 0x3F 0x40 0xDA 0x12 0xAF",
                        "scope": {"controller": "sh1106"},
                        "source_pages": [12],
                        "source_excerpt": "0xAE 0xD5 0x80 0xA8 0x3F 0x40 0xDA 0x12 0xAF",
                        "confidence": 0.95,
                    }
                ],
            }
        ]
    )
    extraction = DeepSeekKnowledgeAtomExtractor(client, "fast-test").extract(
        title="SH1106 数据手册",
        source_uri="docs/sh1106.pdf",
        batches=[_sh1106_batch(window)],
    )
    assert len(extraction.parameters) == 1
    parameter = extraction.parameters[0]
    assert parameter.parameter == "init_sequence"
    assert parameter.value_type == "bytes"
    assert parameter.scope == {"controller": "sh1106"}
    assert parameter.source_section == "命令表 / 初始化"


def test_extractor_drops_parameter_with_non_verbatim_excerpt() -> None:
    """机械闸门：excerpt 不是原文子串 → 丢弃参数原子（摘录而非回忆）。"""
    window = "## 第 12 页\nSH1106 Initialization: 0xAE 0xD5 0x80"
    client = FakeJsonCompletionClient(
        [
            {
                "atoms": [],
                "parameters": [
                    {
                        "parameter": "init_sequence",
                        "value_type": "bytes",
                        "value": "0xAE 0xD5 0x80",
                        "scope": {"controller": "sh1106"},
                        "source_pages": [12],
                        # 模型改写/补全的 excerpt（原文并没有 0xAF）
                        "source_excerpt": "0xAE 0xD5 0x80 0xAF",
                        "confidence": 0.9,
                    }
                ],
            }
        ]
    )
    extraction = DeepSeekKnowledgeAtomExtractor(client, "fast-test").extract(
        title="SH1106 数据手册",
        source_uri="docs/sh1106.pdf",
        batches=[_sh1106_batch(window)],
    )
    assert extraction.parameters == []  # 丢弃而非放行


def test_extractor_drops_bytes_value_not_located_in_excerpt() -> None:
    """bytes 型 value 的每个字节必须能在 excerpt 中定位，否则丢弃。"""
    window = "## 第 12 页\nSH1106 Initialization: 0xAE 0xD5 0x80"
    client = FakeJsonCompletionClient(
        [
            {
                "atoms": [],
                "parameters": [
                    {
                        "parameter": "init_sequence",
                        "value_type": "bytes",
                        # value 含 0xAF 但 excerpt 只有 0xAE 0xD5 0x80
                        "value": "0xAE 0xD5 0x80 0xAF",
                        "scope": {"controller": "sh1106"},
                        "source_pages": [12],
                        "source_excerpt": "0xAE 0xD5 0x80",
                        "confidence": 0.9,
                    }
                ],
            }
        ]
    )
    extraction = DeepSeekKnowledgeAtomExtractor(client, "fast-test").extract(
        title="SH1106 数据手册",
        source_uri="docs/sh1106.pdf",
        batches=[_sh1106_batch(window)],
    )
    assert extraction.parameters == []


def test_extractor_missing_parameters_key_still_works() -> None:
    """旧格式输出（无 parameters 字段）向后兼容。"""
    client = FakeJsonCompletionClient(
        [
            {
                "atoms": [
                    {
                        "subject": "GPIO34",
                        "statement": "GPIO34 只能输入。",
                        "source_pages": [8],
                    }
                ]
            }
        ]
    )
    extraction = DeepSeekKnowledgeAtomExtractor(client, "fast-test").extract(
        title="ESP32",
        source_uri="esp32.pdf",
        batches=[PdfBatch(8, 8, 30, "GPIO34 input only", False)],
    )
    assert len(extraction.atoms) == 1
    assert extraction.parameters == []


def test_verbatim_check_accepts_suffix_hex_notation() -> None:
    """数据手册 AEH/AFh 后缀字节记法应被识别（不误杀正确摘录）。"""
    from luxar.adapters.deepseek.knowledge_extractor import _verbatim_check
    from luxar.domain.knowledge_atoms import ParameterAtomDraft

    window = "11. Display OFF/ON: (AEH - AFH)"
    ok = ParameterAtomDraft(
        parameter="command_display_on_off",
        value_type="bytes",
        value="0xAE 0xAF",
        source_excerpt="11. Display OFF/ON: (AEH - AFH)",
    )
    assert _verbatim_check(ok, window) is True
    wrong = ParameterAtomDraft(
        parameter="command_display_on_off",
        value_type="bytes",
        value="0xAE 0xB0",  # 0xB0 不在 excerpt 中
        source_excerpt="11. Display OFF/ON: (AEH - AFH)",
    )
    assert _verbatim_check(wrong, window) is False


# ---------------------------------------------------------------------------
# sequence 型参数（有序命令序列）
# ---------------------------------------------------------------------------


def test_sequence_parameter_extracts_ordered_commands() -> None:
    """Power up Sequence 应提取为一条有序 sequence 参数，而非拆散。"""
    window = (
        "## 第 12 页\n<Power up Sequence>\nSet Display Off 0xAE\n"
        "Set Display Clock Divide Ratio/Oscillator Frequency 0xD5 0x80\n"
        "Set Segment Re-Map 0xA1\nSet Display On 0xAF"
    )
    client = FakeJsonCompletionClient(
        [
            {
                "atoms": [],
                "parameters": [
                    {
                        "parameter": "power_up_sequence",
                        "value_type": "sequence",
                        "value": [
                            {"cmd": 0xAE},
                            {"cmd": 0xD5, "args": [0x80]},
                            {"cmd": 0xA1},
                            {"cmd": 0xAF},
                        ],
                        "scope": {"device": "1.3寸横屏", "controller": "sh1106"},
                        "source_pages": [12],
                        "source_excerpt": (
                            "Set Display Off 0xAE Set Display Clock Divide "
                            "Ratio/Oscillator Frequency 0xD5 0x80 Set Segment "
                            "Re-Map 0xA1 Set Display On 0xAF"
                        ),
                        "confidence": 0.95,
                    }
                ],
            }
        ]
    )
    extraction = DeepSeekKnowledgeAtomExtractor(client, "fast-test").extract(
        title="1.3寸横屏规格书",
        source_uri="docs/panel.pdf",
        batches=[PdfBatch(12, 12, 12, window, False)],
    )
    assert len(extraction.parameters) == 1
    parameter = extraction.parameters[0]
    assert parameter.value_type == "sequence"
    commands = json.loads(parameter.value)
    assert commands == [
        {"cmd": 0xAE},
        {"cmd": 0xD5, "args": [0x80]},
        {"cmd": 0xA1},
        {"cmd": 0xAF},
    ]
    assert parameter.scope == {"device": "1.3寸横屏", "controller": "sh1106"}


def test_sequence_verbatim_check_validates_each_command_byte() -> None:
    """sequence 的每个命令字节必须在 excerpt 中可定位。"""
    from luxar.adapters.deepseek.knowledge_extractor import _verbatim_check
    from luxar.domain.knowledge_atoms import ParameterAtomDraft

    window = "<Power up Sequence> 0xAE 0xD5 0x80 0xA1 0xAF"
    ok = ParameterAtomDraft(
        parameter="power_up_sequence",
        value_type="sequence",
        value=json.dumps([
            {"cmd": 0xAE},
            {"cmd": 0xD5, "args": [0x80]},
            {"cmd": 0xA1},
            {"cmd": 0xAF},
        ]),
        source_excerpt="0xAE 0xD5 0x80 0xA1 0xAF",
    )
    assert _verbatim_check(ok, window) is True
    # 值含 excerpt 没有的字节 0xC8 → 丢弃
    wrong = ParameterAtomDraft(
        parameter="power_up_sequence",
        value_type="sequence",
        value=json.dumps([
            {"cmd": 0xAE},
            {"cmd": 0xC8},
        ]),
        source_excerpt="0xAE 0xD5 0x80",
    )
    assert _verbatim_check(wrong, window) is False


def test_sequence_must_be_ordered_command_list() -> None:
    """sequence value 必须是 JSON 命令列表（非列表/无 cmd 拒绝）。"""
    from luxar.domain.knowledge_atoms import ParameterAtomDraft
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ParameterAtomDraft(
            parameter="bad_sequence",
            value_type="sequence",
            value="0xAE 0xD5",  # 不是 JSON 列表
            source_excerpt="0xAE 0xD5",
        )
    with pytest.raises(ValidationError):
        ParameterAtomDraft(
            parameter="bad_sequence",
            value_type="sequence",
            value=json.dumps([{"args": [0x80]}]),  # 缺 cmd
            source_excerpt="0xAE",
        )
