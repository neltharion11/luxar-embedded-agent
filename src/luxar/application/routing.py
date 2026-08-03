"""条件路由：只读取结构化 State，决定需求分析或构建之后应前往哪个节点。"""

from __future__ import annotations

from typing import Literal

from luxar.application.state import WorkflowState


def route_after_requirement(
    state: WorkflowState,
) -> Literal["create_plan", "request_clarification"]:
    # 路由不再分析原始自然语言，只读取已经验证过的 FirmwareRequirement。
    requirement = state["requirement"]

    if requirement.is_complete:
        return "create_plan"

    return "request_clarification"


def route_after_build(
    state: WorkflowState,
) -> Literal[
    "completed",
    "repair_project",
    "build_project",
    "failed",
]:
    # Literal 返回类型向编辑器声明：本函数只能选择这四个合法目的地。
    evidence = state["build_evidence"]
    attempts = state.get("attempts", 0)
    max_attempts = state.get("max_attempts", 1)

    # 成功必须最先判断：即使恰好用完最后一次预算，成功结果仍应完成。
    if evidence.success:
        return "completed"

    # 失败且预算耗尽时先终止，防止后续分支形成无限循环。
    if attempts >= max_attempts:
        return "failed"

    # 源码/链接错误在代码不变时重建没有意义，必须先进入修复节点。
    if evidence.error_category in {"source", "linker"}:
        return "repair_project"

    # 超时可能是临时环境波动，因此允许在预算内不改代码直接重建。
    if evidence.error_category == "timeout":
        return "build_project"

    return "failed"
