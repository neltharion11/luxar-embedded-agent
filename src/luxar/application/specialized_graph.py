"""Dedicated graph for project inspection and bounded knowledge operations."""

from __future__ import annotations

from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from luxar.application.specialized_context import SpecializedRuntimeContext
from luxar.application.specialized_nodes import (
    assess_specialized_knowledge_evidence,
    analyze_specialized_knowledge_task,
    analyze_specialized_project,
    complete_specialized_workflow,
    execute_specialized_knowledge_task,
    expand_specialized_knowledge_query,
    fail_specialized_workflow,
    report_specialized_project,
    normalize_specialized_knowledge_evidence,
    prepare_project_knowledge_retrieval,
    retrieve_specialized_knowledge_evidence,
    revise_specialized_knowledge_answer,
    route_specialized_knowledge_action,
    review_specialized_knowledge_task,
    synthesize_specialized_knowledge_answer,
    verify_specialized_knowledge_answer,
)
from luxar.application.specialized_state import SpecializedWorkflowState


def _route_from_start(
    state: SpecializedWorkflowState,
) -> Literal["analyze_project", "analyze_knowledge_task"]:
    return (
        "analyze_project"
        if state.get("task_mode") == "inspection"
        else "analyze_knowledge_task"
    )


def _route_after_knowledge_review(
    state: SpecializedWorkflowState,
) -> Literal["route_knowledge_action", "analyze_knowledge_task", "failed"]:
    action = state.get("interaction_action")
    if action == "continue":
        return "route_knowledge_action"
    if action == "replan":
        return "analyze_knowledge_task"
    return "failed"


def _route_after_project_analysis(
    state: SpecializedWorkflowState,
) -> Literal["report_project", "prepare_project_knowledge_retrieval"]:
    return (
        "prepare_project_knowledge_retrieval"
        if state.get("knowledge_retrieval_selected", False)
        else "report_project"
    )


def _route_knowledge_action(
    state: SpecializedWorkflowState,
) -> Literal["retrieve_knowledge_evidence", "execute_knowledge_task"]:
    return (
        "retrieve_knowledge_evidence"
        if state["knowledge_task"].action == "search"
        else "execute_knowledge_task"
    )


def _route_after_evidence_assessment(
    state: SpecializedWorkflowState,
) -> Literal["synthesize_grounded_answer", "expand_knowledge_query", "failed"]:
    assessment = state.get("evidence_assessment", {})
    if bool(assessment.get("sufficient")):
        return "synthesize_grounded_answer"
    if int(state.get("retrieval_round", 0)) < 2:
        return "expand_knowledge_query"
    return "failed"


def _route_after_answer_verification(
    state: SpecializedWorkflowState,
) -> Literal["completed", "revise_grounded_answer", "failed"]:
    verification = state.get("answer_verification", {})
    if bool(verification.get("passed")):
        return "completed"
    if int(state.get("answer_revision_count", 0)) < 2:
        return "revise_grounded_answer"
    return "failed"


def build_specialized_graph(
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    builder = StateGraph(
        SpecializedWorkflowState,
        context_schema=SpecializedRuntimeContext,
    )
    builder.add_node("analyze_project", analyze_specialized_project)
    builder.add_node("report_project", report_specialized_project)
    builder.add_node(
        "prepare_project_knowledge_retrieval",
        prepare_project_knowledge_retrieval,
    )
    builder.add_node(
        "analyze_knowledge_task",
        analyze_specialized_knowledge_task,
    )
    builder.add_node(
        "review_knowledge_task",
        review_specialized_knowledge_task,
    )
    builder.add_node(
        "execute_knowledge_task",
        execute_specialized_knowledge_task,
    )
    builder.add_node("route_knowledge_action", route_specialized_knowledge_action)
    builder.add_node(
        "retrieve_knowledge_evidence",
        retrieve_specialized_knowledge_evidence,
    )
    builder.add_node(
        "normalize_knowledge_evidence",
        normalize_specialized_knowledge_evidence,
    )
    builder.add_node(
        "assess_evidence_sufficiency",
        assess_specialized_knowledge_evidence,
    )
    builder.add_node("expand_knowledge_query", expand_specialized_knowledge_query)
    builder.add_node(
        "synthesize_grounded_answer",
        synthesize_specialized_knowledge_answer,
    )
    builder.add_node("verify_grounded_answer", verify_specialized_knowledge_answer)
    builder.add_node("revise_grounded_answer", revise_specialized_knowledge_answer)
    builder.add_node("completed", complete_specialized_workflow)
    builder.add_node("failed", fail_specialized_workflow)

    builder.add_conditional_edges(
        START,
        _route_from_start,
        {
            "analyze_project": "analyze_project",
            "analyze_knowledge_task": "analyze_knowledge_task",
        },
    )
    builder.add_conditional_edges(
        "analyze_project",
        _route_after_project_analysis,
        {
            "report_project": "report_project",
            "prepare_project_knowledge_retrieval": (
                "prepare_project_knowledge_retrieval"
            ),
        },
    )
    builder.add_edge("report_project", END)
    builder.add_edge(
        "prepare_project_knowledge_retrieval",
        "retrieve_knowledge_evidence",
    )
    builder.add_edge("analyze_knowledge_task", "review_knowledge_task")
    builder.add_conditional_edges(
        "review_knowledge_task",
        _route_after_knowledge_review,
        {
            "route_knowledge_action": "route_knowledge_action",
            "analyze_knowledge_task": "analyze_knowledge_task",
            "failed": "failed",
        },
    )
    builder.add_conditional_edges(
        "route_knowledge_action",
        _route_knowledge_action,
        {
            "retrieve_knowledge_evidence": "retrieve_knowledge_evidence",
            "execute_knowledge_task": "execute_knowledge_task",
        },
    )
    builder.add_edge("retrieve_knowledge_evidence", "normalize_knowledge_evidence")
    builder.add_edge("normalize_knowledge_evidence", "assess_evidence_sufficiency")
    builder.add_conditional_edges(
        "assess_evidence_sufficiency",
        _route_after_evidence_assessment,
        {
            "synthesize_grounded_answer": "synthesize_grounded_answer",
            "expand_knowledge_query": "expand_knowledge_query",
            "failed": "failed",
        },
    )
    builder.add_edge("expand_knowledge_query", "retrieve_knowledge_evidence")
    builder.add_edge("synthesize_grounded_answer", "verify_grounded_answer")
    builder.add_conditional_edges(
        "verify_grounded_answer",
        _route_after_answer_verification,
        {
            "completed": "completed",
            "revise_grounded_answer": "revise_grounded_answer",
            "failed": "failed",
        },
    )
    builder.add_edge("revise_grounded_answer", "verify_grounded_answer")
    builder.add_edge("execute_knowledge_task", "completed")
    builder.add_edge("completed", END)
    builder.add_edge("failed", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_specialized_graph"]
