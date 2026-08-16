"""Graph 装配器：注册业务节点、普通边和条件边，并编译为可执行工作流。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from luxar.application.context import RuntimeContext
from luxar.application.nodes import (
    analyze_requirement,
    build_project,
    completed,
    create_plan,
    execute_next_step,
    failed,
    repair_project,
    request_clarification,
)
from luxar.application.routing import (
    route_after_build,
    route_after_dispatch,
    route_after_requirement,
)
from luxar.application.state import WorkflowState


def build_graph() -> CompiledStateGraph:
    # StateGraph 在编译期只需要 State/Context 的类型，不创建任何具体 Adapter。
    builder = StateGraph(
        WorkflowState,
        context_schema=RuntimeContext,
    )

    # add_node 的字符串是图内名称，第二个参数是实际执行的 Python 函数。
    builder.add_node(
        "analyze_requirement",
        analyze_requirement,
    )
    builder.add_node(
        "create_plan",
        create_plan,
    )
    builder.add_node(
        "execute_next_step",
        execute_next_step,
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

    # START 是 LangGraph 的虚拟入口；需求分析永远是第一个业务节点。
    builder.add_edge(
        START,
        "analyze_requirement",
    )

    # 条件边先调用路由函数，再用映射表把返回字符串解析为真实节点。
    builder.add_conditional_edges(
        "analyze_requirement",
        route_after_requirement,
        {
            "create_plan": "create_plan",
            "request_clarification": "request_clarification",
        },
    )

    # 计划生成后进入游标分发器，由已验证的步骤决定下一个动作。
    builder.add_edge(
        "create_plan",
        "execute_next_step",
    )

    # S1 只放开 build_project；其余词表步骤由分发器写出固定错误并路由到 failed。
    builder.add_conditional_edges(
        "execute_next_step",
        route_after_dispatch,
        {
            "build_project": "build_project",
            "completed": "completed",
            "failed": "failed",
        },
    )

    # 构建后可能继续计划、修复、原样重试或失败，其中自环代表 timeout 重试。
    builder.add_conditional_edges(
        "build_project",
        route_after_build,
        {
            "execute_next_step": "execute_next_step",
            "repair_project": "repair_project",
            "build_project": "build_project",
            "failed": "failed",
        },
    )

    # 修复完成后必须重新构建，由新的工具证据证明是否真的修好。
    builder.add_edge(
        "repair_project",
        "build_project",
    )

    # 三个业务终态都连接到 LangGraph 的虚拟 END 节点。
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

    # compile 会检查并冻结拓扑，返回能够 invoke/stream 的 CompiledStateGraph。
    return builder.compile()
