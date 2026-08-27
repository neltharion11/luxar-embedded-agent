from __future__ import annotations

from luxar.database.persistence import KnowledgeMatch
from luxar.domain.knowledge_answers import GroundedKnowledgeAnswer, KnowledgeEvidence
from luxar.knowledge_answering import (
    prepare_knowledge_evidence,
    verify_grounded_answer,
)


def _match(*, knowledge_id: str, document_id: str, content: str, score: float):
    return KnowledgeMatch(
        document_id=document_id,
        title="ESP32 数据手册",
        source_uri="docs/esp32.pdf",
        ordinal=0,
        content=content,
        score=score,
        knowledge_id=knowledge_id,
        subject="GPIO34",
        category="pin",
        source_pages=(8,),
    )


def test_evidence_preparation_deduplicates_atoms_and_limits_one_document() -> None:
    matches = [
        _match(
            knowledge_id="ka-duplicate",
            document_id="doc-a",
            content="GPIO34 只能作为输入使用。",
            score=0.99,
        ),
        _match(
            knowledge_id="ka-duplicate",
            document_id="doc-a",
            content="GPIO34 只能作为输入使用。",
            score=0.98,
        ),
        *[
            _match(
                knowledge_id=f"ka-{index}",
                document_id="doc-a",
                content=f"GPIO{index} 是普通输入输出引脚。",
                score=0.9 - index / 100,
            )
            for index in range(4)
        ],
        _match(
            knowledge_id="ka-other",
            document_id="doc-b",
            content="ADC2 与 Wi-Fi 存在资源占用关系。",
            score=0.7,
        ),
    ]

    evidence = prepare_knowledge_evidence(matches, per_document_limit=3)

    assert len({item.knowledge_id for item in evidence}) == len(evidence)
    assert sum(item.document_id == "doc-a" for item in evidence) == 3
    assert any(item.document_id == "doc-b" for item in evidence)
    assert [item.evidence_id for item in evidence] == [
        f"E{index}" for index in range(1, len(evidence) + 1)
    ]


def test_verifier_rejects_source_title_list_as_an_answer() -> None:
    evidence = [
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
            score=0.9,
        )
    ]
    answer = GroundedKnowledgeAnswer(
        answer_markdown="检索完成，相关来源为 ESP32 数据手册，请查看原文。[E1]",
        cited_evidence_ids=["E1"],
    )

    verification = verify_grounded_answer(answer, evidence)

    assert verification.passed is False
    assert "no_grounded_fact_content" in verification.issue_codes


def test_verifier_rejects_unknown_citation_and_uncited_pin_claim() -> None:
    evidence = [
        KnowledgeEvidence(
            evidence_id="E1",
            document_id="doc-1",
            title="ESP32 数据手册",
            source_uri="docs/esp32.pdf",
            subject="GPIO34",
            statement="GPIO34 只能作为输入使用。",
            score=0.9,
        )
    ]
    answer = GroundedKnowledgeAnswer(
        answer_markdown="GPIO34 只能作为输入使用。\n另一个结论。[E9]",
        cited_evidence_ids=["E9"],
    )

    verification = verify_grounded_answer(answer, evidence)

    assert "invalid_citations" in verification.issue_codes
    assert "uncited_technical_claims" in verification.issue_codes
