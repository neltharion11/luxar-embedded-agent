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

    atoms = extractor.extract(
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

    assert atoms[0].subject == "GPIO34"
    assert atoms[0].limitations == ["不能配置为输出"]
    assert atoms[0].source_section == "硬件接口 / 引脚定义"
    system_prompt, user_prompt, model = client.calls[0]
    assert "页码列表" in system_prompt
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
    assert model == "quality-test"
