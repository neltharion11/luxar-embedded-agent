"""Graph 装配器：注册业务节点、普通边和条件边，并编译为可执行工作流。"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from luxar.application.context import RuntimeContext
from luxar.application.nodes import (
    analyze_device_logs,
    analyze_requirement,
    build_project,
    completed,
    create_plan,
    create_project,
    execute_next_step,
    failed,
    flash_project,
    monitor_project,
    repair_project,
    request_clarification,
    request_flash_approval,
)
from luxar.application.routing import (
    route_after_approval,
    route_after_build,
    route_after_diagnosis,
    route_after_dispatch,
    route_after_flash,
    route_after_project_creation,
    route_after_requirement,
)
from luxar.application.state import WorkflowState


def build_graph(
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
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
        "create_project",
        create_project,
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
        "request_flash_approval",
        request_flash_approval,
    )
    builder.add_node(
        "flash_project",
        flash_project,
    )
    builder.add_node(
        "monitor_project",
        monitor_project,
    )
    builder.add_node(
        "analyze_device_logs",
        analyze_device_logs,
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

    # 未实现词表步骤由分发器写出固定错误并路由到 failed。
    builder.add_conditional_edges(
        "execute_next_step",
        route_after_dispatch,
        {
            "create_project": "create_project",
            "build_project": "build_project",
            "request_flash_approval": "request_flash_approval",
            "monitor_project": "monitor_project",
            "completed": "completed",
            "failed": "failed",
        },
    )

    # 创建成功后回到游标继续计划；失败证据直接终止。
    builder.add_conditional_edges(
        "create_project",
        route_after_project_creation,
        {
            "execute_next_step": "execute_next_step",
            "failed": "failed",
        },
    )

    # 构建后可能继续计划、进入设备回路、修复、原样重试或失败。
    builder.add_conditional_edges(
        "build_project",
        route_after_build,
        {
            "execute_next_step": "execute_next_step",
            "request_flash_approval": "request_flash_approval",
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

    # 烧录前必须经过人工审批；批准后执行真实烧录，失败按类别重试或终止。
    builder.add_conditional_edges(
        "request_flash_approval",
        route_after_approval,
        {
            "flash_project": "flash_project",
            "failed": "failed",
        },
    )
    builder.add_conditional_edges(
        "flash_project",
        route_after_flash,
        {
            "execute_next_step": "execute_next_step",
            "monitor_project": "monitor_project",
            "flash_project": "flash_project",
            "failed": "failed",
        },
    )

    # 监控采集结束后进入日志分析；健康完成，需要修复进入设备回路。
    builder.add_edge(
        "monitor_project",
        "analyze_device_logs",
    )
    builder.add_conditional_edges(
        "analyze_device_logs",
        route_after_diagnosis,
        {
            "repair_project": "repair_project",
            "completed": "completed",
            "failed": "failed",
        },
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
    # interrupt() 需要 checkpointer；未提供时本图不支持中断。
    return builder.compile(checkpointer=checkpointer)
