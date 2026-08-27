"""Evidence preparation, deterministic fallback answering and verification."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence

from luxar.database.persistence import KnowledgeMatch
from luxar.domain.knowledge_answers import (
    EvidenceAssessment,
    GroundedKnowledgeAnswer,
    KnowledgeAnswerVerification,
    KnowledgeEvidence,
)


_CITATION_RE = re.compile(r"\[(E\d+)\]")
_TECHNICAL_NUMBER_RE = re.compile(
    r"(?:(?:GPIO|IO|ADC|DAC|UART|I2C|SPI)\s*\d+|"
    r"\d+(?:\.\d+)?\s*(?:V|mA|A|MHz|kHz|Hz|位|字节))",
    re.IGNORECASE,
)
_ANSWER_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+-]*|[\u4e00-\u9fff]{2}")


def prepare_knowledge_evidence(
    matches: Sequence[KnowledgeMatch],
    *,
    limit: int = 8,
    max_characters: int = 18_000,
    per_document_limit: int = 3,
) -> list[KnowledgeEvidence]:
    """Deduplicate and diversify concrete facts under a hard evidence budget."""

    selected: list[KnowledgeMatch] = []
    seen: set[str] = set()
    per_document: defaultdict[str, int] = defaultdict(int)
    characters = 0
    for match in sorted(matches, key=lambda item: item.score, reverse=True):
        normalized = re.sub(r"\s+", " ", match.content).strip().casefold()
        identity = match.knowledge_id or hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()
        if not normalized or identity in seen:
            continue
        if per_document[match.document_id] >= per_document_limit:
            continue
        if selected and characters + len(match.content) > max_characters:
            continue
        seen.add(identity)
        per_document[match.document_id] += 1
        characters += len(match.content)
        selected.append(match)
        if len(selected) >= limit:
            break

    return [
        KnowledgeEvidence(
            evidence_id=f"E{index}",
            knowledge_id=match.knowledge_id,
            document_id=match.document_id,
            title=match.title,
            source_uri=match.source_uri,
            subject=match.subject or match.title,
            statement=match.content,
            category=match.category or "general",
            source_pages=list(match.source_pages),
            source_section=match.source_section,
            applicable_conditions=list(match.applicable_conditions),
            limitations=list(match.limitations),
            score=match.score,
        )
        for index, match in enumerate(selected, start=1)
    ]


def assess_knowledge_evidence(
    question: str,
    evidence: Sequence[KnowledgeEvidence],
    *,
    retrieval_round: int,
) -> EvidenceAssessment:
    broad = bool(re.search(r"各|所有|全部|全面|完整|分别", question))
    categories = {item.category for item in evidence if item.category != "general"}
    documents = {item.document_id for item in evidence}
    if not evidence:
        return EvidenceAssessment(
            sufficient=False,
            reason="没有找到可用于回答的具体知识",
            missing_facets=["与问题直接相关的事实"],
        )
    if broad and len(evidence) < 3 and retrieval_round < 2:
        return EvidenceAssessment(
            sufficient=False,
            reason="问题要求全面说明，但当前知识覆盖面不足",
            missing_facets=["更多功能类别或限制条件"],
            evidence_count=len(evidence),
            document_count=len(documents),
        )
    return EvidenceAssessment(
        sufficient=True,
        reason=(
            "已找到能够直接支撑回答的具体知识"
            if categories
            else "已找到相关事实；答案将明确标注资料覆盖范围"
        ),
        evidence_count=len(evidence),
        document_count=len(documents),
    )


def expand_knowledge_query(question: str, retrieval_round: int) -> str:
    expansions: list[str] = []
    lowered = question.casefold()
    if "引脚" in question or "gpio" in lowered:
        expansions.extend(
            [
                "GPIO 输入 输出 复用功能",
                "启动绑带 strapping 输入专用",
                "ADC DAC Touch UART I2C SPI 限制",
            ]
        )
    if "esp32" in lowered:
        expansions.extend(["ESP32 芯片 模组 型号差异", "Flash PSRAM 占用引脚"])
    if retrieval_round > 1:
        expansions.append("适用条件 例外 禁止 注意事项 资源冲突")
    return " ".join([question, *expansions]).strip()


class EvidenceListKnowledgeAnswerer:
    """Safe fallback that still answers with facts instead of source titles."""

    def answer(
        self,
        *,
        question: str,
        evidence: Sequence[KnowledgeEvidence],
        revision_instructions: str = "",
        response_plan: Mapping[str, object] | None = None,
        conversation_context: Sequence[Mapping[str, str]] | None = None,
    ) -> GroundedKnowledgeAnswer:
        del revision_instructions
        del conversation_context
        plan = response_plan or {}
        answer_budget = int(plan.get("answer_budget", 600))
        item_limit = max(1, min(len(evidence), answer_budget // 250))
        selected_evidence = list(evidence[:item_limit])
        grouped: defaultdict[str, list[KnowledgeEvidence]] = defaultdict(list)
        for item in selected_evidence:
            grouped[item.category].append(item)
        source_scope = (
            "当前代码与检索证据"
            if any(item.source_uri.startswith("project://") for item in evidence)
            else "当前知识库"
        )
        # 问题本身可能含 GPIO 编号等技术事实；不要在无引用的标题中复述，
        # 否则严格验证器会把标题误判为未引用的技术结论。
        lines = [f"根据{source_scope}，可以确认："]
        for category, items in grouped.items():
            lines.extend(["", f"### {category}", ""])
            for item in items:
                detail = re.sub(r"\s+", " ", item.statement).strip()
                if item.applicable_conditions:
                    detail += "；适用条件：" + "、".join(item.applicable_conditions)
                if item.limitations:
                    detail += "；限制：" + "、".join(item.limitations)
                lines.append(f"- {item.subject}：{detail} [{item.evidence_id}]")
        return GroundedKnowledgeAnswer(
            answer_markdown="\n".join(lines),
            cited_evidence_ids=[item.evidence_id for item in selected_evidence],
            coverage_summary=f"使用了 {len(selected_evidence)} 条知识证据。",
            uncertainties=["答案仅覆盖当前知识库能够证明的内容。"],
        )


def verify_grounded_answer(
    answer: GroundedKnowledgeAnswer,
    evidence: Sequence[KnowledgeEvidence],
) -> KnowledgeAnswerVerification:
    valid_ids = {item.evidence_id for item in evidence}
    cited = set(_CITATION_RE.findall(answer.answer_markdown))
    declared = set(answer.cited_evidence_ids)
    invalid = sorted((cited | declared) - valid_ids)
    issues: list[str] = []
    unsupported: list[str] = []
    if len(answer.answer_markdown.strip()) < 12:
        issues.append("answer_too_short")
    if not cited:
        issues.append("missing_citations")
    if invalid:
        issues.append("invalid_citations")
    answer_tokens = {
        token.casefold() for token in _ANSWER_TOKEN_RE.findall(answer.answer_markdown)
    }
    grounded_content = any(
        item.subject.casefold() in answer.answer_markdown.casefold()
        or len(
            answer_tokens
            & {
                token.casefold()
                for token in _ANSWER_TOKEN_RE.findall(item.statement)
            }
        )
        >= 2
        for item in evidence
    )
    if not grounded_content:
        issues.append("no_grounded_fact_content")
    for line in answer.answer_markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _TECHNICAL_NUMBER_RE.search(stripped) and not _CITATION_RE.search(stripped):
            unsupported.append(stripped[:500])
    if unsupported:
        issues.append("uncited_technical_claims")
    instructions: list[str] = []
    if "answer_too_short" in issues:
        instructions.append("给出包含具体事实、适用范围和限制条件的完整回答")
    if "missing_citations" in issues:
        instructions.append("为事实结论添加对应的 [E编号] 引用")
    if invalid:
        instructions.append("删除或替换不存在的证据编号：" + "、".join(invalid))
    if unsupported:
        instructions.append("为包含引脚号或数值的结论逐行补充证据引用")
    if "no_grounded_fact_content" in issues:
        instructions.append("直接回答问题并写出证据中的具体事实，不能只列来源标题")
    if issues and not instructions:
        instructions.append("根据验证问题重新生成证据约束答案")
    return KnowledgeAnswerVerification(
        passed=not issues,
        issue_codes=issues,
        invalid_citations=invalid,
        unsupported_claims=unsupported,
        revision_instructions="；".join(instructions),
    )


__all__ = [
    "EvidenceListKnowledgeAnswerer",
    "assess_knowledge_evidence",
    "expand_knowledge_query",
    "prepare_knowledge_evidence",
    "verify_grounded_answer",
]
