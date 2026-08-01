from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from luxar.application.context import RuntimeContext
from luxar.application.nodes import (
    analyze_requirement,
    build_project,
    completed,
    create_plan,
    failed,
    repair_project,
    request_clarification,
)
from luxar.application.routing import (
    route_after_build,
    route_after_requirement,
)
from luxar.application.state import WorkflowState


def build_graph() -> CompiledStateGraph:
    builder = StateGraph(
        WorkflowState,
        context_schema=RuntimeContext,
    )

    builder.add_node(
        "analyze_requirement",
        analyze_requirement,
    )
    builder.add_node(
        "create_plan",
        create_plan,
    )
    builder.add_node(
        "build_project",
        build_project,
    )
    builder.add_node(
        "repair_project",
        repair_project,
    )
    builder.add_node(
        "request_clarification",
        request_clarification,
    )
    builder.add_node(
        "completed",
        completed,
    )
    builder.add_node(
        "failed",
        failed,
    )

    builder.add_edge(
        START,
        "analyze_requirement",
    )

    builder.add_conditional_edges(
        "analyze_requirement",
        route_after_requirement,
        {
            "create_plan": "create_plan",
            "request_clarification": "request_clarification",
        },
    )

    builder.add_edge(
        "create_plan",
        "build_project",
    )

    builder.add_conditional_edges(
        "build_project",
        route_after_build,
        {
            "completed": "completed",
            "repair_project": "repair_project",
            "build_project": "build_project",
            "failed": "failed",
        },
    )

    builder.add_edge(
        "repair_project",
        "build_project",
    )

    builder.add_edge(
        "request_clarification",
        END,
    )
    builder.add_edge(
        "completed",
        END,
    )
    builder.add_edge(
        "failed",
        END,
    )

    return builder.compile()