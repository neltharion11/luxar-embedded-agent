"""条件路由：只读取结构化 State，决定每个分支节点之后应前往哪个节点。"""

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


def route_after_dispatch(
    state: WorkflowState,
) -> Literal[
    "create_project",
    "build_project",
    "request_flash_approval",
    "monitor_project",
    "completed",
    "failed",
]:
    # 已实现步骤进入对应节点；未实现步骤由分发器写入固定错误并路由到 failed。
    pending = state.get("pending_step_kind")

    if pending == "create_project":
        return "create_project"

    if pending == "build_project":
        return "build_project"

    if pending == "flash_project":
        return "request_flash_approval"

    if pending == "monitor_project":
        return "monitor_project"

    # 计划执行完毕时分发器写入 None，进入 completed 终态。
    if pending is None:
        return "completed"

    return "failed"


def route_after_project_creation(
    state: WorkflowState,
) -> Literal["execute_next_step", "failed"]:
    # 与构建相同：成功与否只能由创建证据决定，失败直接终止。
    evidence = state["created_project"]

    if evidence.success:
        return "execute_next_step"

    return "failed"


def route_after_approval(
    state: WorkflowState,
) -> Literal["flash_project", "failed"]:
    # 批准后真正执行烧录；拒绝或审批失败都终止。
    if state.get("approval_status") == "approved":
        return "flash_project"

    return "failed"


def route_after_flash(
    state: WorkflowState,
) -> Literal[
    "execute_next_step",
    "monitor_project",
    "flash_project",
    "failed",
]:
    # 烧录成功继续计划；串口/超时在预算内直接重试一次，其余失败终止。
    evidence = state["flash_evidence"]
    attempts = state.get("flash_attempts", 0)

    if evidence.success:
        # 设备回路中的重烧录必须回到监控，而不是重新走计划游标。
        if state.get("repair_origin") == "monitor":
            return "monitor_project"

        return "execute_next_step"

    if attempts >= 2:
        return "failed"

    if evidence.error_category in {"serial", "timeout"}:
        return "flash_project"

    return "failed"


def route_after_diagnosis(
    state: WorkflowState,
) -> Literal["repair_project", "completed", "failed"]:
    # 健康即完成；需要修复进入设备回路；其余情况终止（节点已写入错误）。
    diagnosis = state["device_diagnosis"]

    if diagnosis.healthy:
        return "completed"

    if diagnosis.repair_needed and "error" not in state:
        return "repair_project"

    return "failed"


def route_after_build(
    state: WorkflowState,
) -> Literal[
    "execute_next_step",
    "request_flash_approval",
    "repair_project",
    "build_project",
    "failed",
]:
    # Literal 返回类型向编辑器声明：本函数只能选择这五个合法目的地。
    evidence = state["build_evidence"]
    attempts = state.get("attempts", 0)
    max_attempts = state.get("max_attempts", 1)

    # 成功必须最先判断：即使恰好用完最后一次预算，成功结果仍应继续计划。
    if evidence.success:
        # 设备回路修复后的重建需要重新烧录验证。
        if state.get("repair_origin") == "monitor":
            return "request_flash_approval"

        return "execute_next_step"

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
