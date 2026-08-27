"""State contract for read-only inspection and bounded knowledge workflows."""

from __future__ import annotations

from typing import Literal, TypedDict

from luxar.domain.errors import WorkflowError
from luxar.domain.interactions import WorkflowInteraction
from luxar.domain.knowledge_tasks import KnowledgeTask
from luxar.domain.project_analysis import ProjectAnalysis


class SpecializedWorkflowState(TypedDict, total=False):
    task_text: str
    task_mode: Literal["inspection", "knowledge"]
    response_plan: dict[str, object]
    knowledge_retrieval_selected: bool
    knowledge_retrieval_reason: str
    knowledge_retrieval_query: str
    conversation_context: list[dict[str, str]]
    focused_response: str
    project_analysis: ProjectAnalysis
    inspection_response: str
    knowledge_task: KnowledgeTask
    knowledge_result: dict[str, object]
    knowledge_queries: list[str]
    retrieval_matches: list[dict[str, object]]
    knowledge_evidence: list[dict[str, object]]
    evidence_assessment: dict[str, object]
    knowledge_answer: dict[str, object]
    answer_verification: dict[str, object]
    retrieval_round: int
    answer_revision_count: int
    interaction: WorkflowInteraction
    interaction_action: Literal["continue", "replan", "failed"]
    status: Literal["running", "completed", "failed", "awaiting_user"]
    error: WorkflowError
    trace: list[str]


__all__ = ["SpecializedWorkflowState"]
