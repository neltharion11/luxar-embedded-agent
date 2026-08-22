"""LangGraph 业务节点：通过 Runtime 中的 Ports 执行动作，并返回最小 State 更新。"""

from __future__ import annotations

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from luxar.application.context import RuntimeContext
from luxar.application.project_analysis import (
    analyze_current_project,
    render_project_analysis,
)
from luxar.application.state import WorkflowState
from luxar.domain.devices import ApprovalRequest
from luxar.domain.errors import WorkflowError
from luxar.ports.errors import CapabilityError
from luxar.ports.espidf_errors import EspIdfError


# 已实现步骤词表随切片扩展；未进入本表的步骤会被分发器拒绝。
_SUPPORTED_STEP_KINDS = frozenset(
    {
        "create_project",
        "implement_change",
        "build_project",
        "flash_project",
        "monitor_project",
    }
)

_UNSUPPORTED_STEP_MESSAGE = "执行计划包含当前版本不支持的步骤"

_UNSUPPORTED_STEP_SUGGESTION = "请更换模型或升级 LUXAR 后重试"

_APPROVAL_SUMMARY = "即将向串口设备烧录固件，请确认目标芯片与串口"

_APPROVAL_REJECTED_MESSAGE = "烧录申请被用户拒绝"

_APPROVAL_REJECTED_SUGGESTION = "确认目标芯片和串口后重新运行任务"


def analyze_requirement(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    # runtime.context 由 LangGraph 在调用节点时注入；具体对象可以是 Fake 或 DeepSeek Adapter。
    requirement = runtime.context.requirement_parser.parse(
        state["task_text"]
    )
    # 项目创建/选择时固定的芯片是可信结构化上下文，不要求用户在每条
    # 自然语言需求中重复声明，也不允许模型用空值覆盖它。
    if runtime.context.target_chip:
        requirement = requirement.model_copy(
            update={
                "target": runtime.context.target_chip,
                "missing_fields": [
                    field
                    for field in requirement.missing_fields
                    if field != "target"
                ],
            }
        )

    # 节点返回局部更新而不是完整 State；LangGraph 会把这些键合并回当前状态。
    return {
        "requirement": requirement,
        "status": "requirement_analyzed",
        "trace": [
            # * 展开旧列表，再追加当前节点名；不会原地修改传入的 trace。
            *state.get("trace", []),
            "analyze_requirement",
        ],
    }


def create_plan(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    requirement = state["requirement"]
    # State 提供业务输入，Runtime Context 提供完成动作所需的外部能力。
    planner = runtime.context.planner
    # 规划器必须看到刚刚验证过的项目快照，不能只凭用户一句话猜测现状。
    plan = planner.create_plan(
        requirement,
        project_analysis=state["project_analysis"],
    )

    return {
        "plan": plan,
        "status": "planned",
        "trace": [
            *state.get("trace", []),
            "create_plan",
        ],
    }


def analyze_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    """Create or reuse the validated snapshot used by every later decision."""

    context = runtime.context
    analysis = analyze_current_project(
        project_path=context.project_path,
        target_chip=context.target_chip,
        workspace=context.workspace,
        analyzer=context.project_analyzer,
        persistence=context.persistence,
        project_key=context.project_key,
    )
    update: dict[str, object] = {
        "project_analysis": analysis,
        "status": "project_analyzed",
        "trace": [*state.get("trace", []), "analyze_project"],
    }

    # A successful creator followed by a missing project is contradictory tool
    # evidence. Stop instead of planning against an imaginary workspace.
    created = state.get("created_project")
    if created is not None and created.success and not analysis.project_exists:
        update["error"] = WorkflowError.model_validate(
            {
                "stage": "planning",
                "category": "workspace",
                "message": "项目创建成功后仍无法读取项目目录",
                "retryable": False,
                "user_suggestion": "请检查项目目录权限和创建工具输出",
            }
        )
    return update


def report_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    """Render the same analysis object used by planning as human prose."""

    return {
        "inspection_response": render_project_analysis(
            runtime.context.project_path.name,
            state["project_analysis"],
        ),
        "status": "completed",
        "trace": [*state.get("trace", []), "report_project"],
    }


def execute_next_step(
    state: WorkflowState,
) -> dict[str, object]:
    # 计划游标分发器：只负责读取已验证的计划并推进游标，不调用任何外部能力。
    plan = state["plan"]
    index = state.get("plan_index", 0)
    trace = [
        *state.get("trace", []),
        "execute_next_step",
    ]

    # 所有步骤都已执行完毕：清空待分发步骤，路由到 completed 终态节点。
    if index >= len(plan.steps):
        return {
            "pending_step_kind": None,
            "trace": trace,
        }

    # 分发前推进游标，因此步骤节点永远不需要管理 plan_index。
    step = plan.steps[index]
    update: dict[str, object] = {
        "plan_index": index + 1,
        "pending_step_kind": step.kind,
        "trace": trace,
    }

    # 词表已由领域模型验证；此处只拒绝当前版本尚未实现的步骤。
    if step.kind not in _SUPPORTED_STEP_KINDS:
        update["error"] = WorkflowError.model_validate(
            {
                "stage": "planning",
                "category": "model_output",
                "message": f"{_UNSUPPORTED_STEP_MESSAGE}：{step.kind}",
                "retryable": False,
                "user_suggestion": _UNSUPPORTED_STEP_SUGGESTION,
            }
        )

    return update


def create_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    # 项目只能创建在 Context 指定的父目录内；芯片优先采用显式配置。
    project_path = runtime.context.project_path
    target_chip = (
        runtime.context.target_chip
        or state["requirement"].target
    )
    evidence = runtime.context.project_creator.create_project(
        parent_dir=project_path.parent,
        project_name=project_path.name,
        target_chip=target_chip,
    )

    return {
        "created_project": evidence,
        "status": "project_created",
        "trace": [
            *state.get("trace", []),
            "create_project",
        ],
    }


def implement_change(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    """Implement the requested change from current code, then refresh its snapshot."""

    context = runtime.context
    editor = context.firmware_editor
    if editor is None:
        raise CapabilityError(
            category="service",
            message="未配置固件代码实现能力",
            retryable=False,
        )

    files = context.workspace.read_project_files(context.project_path)
    references = state.get("reference_examples", [])
    reference_files = []
    if context.example_library is not None:
        for reference in references:
            reference_files.extend(context.example_library.read(reference))
    change = editor.create_change(
        state["requirement"],
        state["project_analysis"],
        files,
        references,
        reference_files,
    )
    changed_now = context.workspace.apply_repair(
        context.project_path,
        change,
    )
    refreshed = analyze_current_project(
        project_path=context.project_path,
        target_chip=context.target_chip,
        workspace=context.workspace,
        analyzer=context.project_analyzer,
        persistence=context.persistence,
        project_key=context.project_key,
        force=True,
    )

    return {
        "implementation_plan": change,
        "changed_files": [
            *state.get("changed_files", []),
            *changed_now,
        ],
        "project_analysis": refreshed,
        "status": "implemented",
        "trace": [*state.get("trace", []), "implement_change"],
    }


def find_idf_examples(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    """Select official SDK examples before the first requirement-driven edit."""

    library = runtime.context.example_library
    references = (
        library.search(state["requirement"], limit=2)
        if library is not None
        else []
    )
    return {
        "reference_examples": references,
        "trace": [*state.get("trace", []), "find_idf_examples"],
    }


def request_flash_approval(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    trace = [
        *state.get("trace", []),
        "request_flash_approval",
    ]

    # 本次运行已经批准过烧录：设备修复回路的重烧录不再重复询问。
    if state.get("approval_status") == "approved":
        return {"trace": trace}

    port = runtime.context.serial_port
    if port is None:
        # 缺少串口是运行配置问题，不是用户决策，直接以脱敏错误终止。
        return {
            "error": WorkflowError.model_validate(
                {
                    "stage": "flash",
                    "category": "serial",
                    "message": "未配置烧录串口",
                    "retryable": False,
                    "user_suggestion": "请通过 --port 指定开发板串口",
                }
            ),
            "trace": trace,
        }

    request = ApprovalRequest(
        project_name=runtime.context.project_path.name,
        port=port,
        target_chip=(
            runtime.context.target_chip
            or state["requirement"].target
        ),
        summary=_APPROVAL_SUMMARY,
        step_description="flash_project",
        attempts=state.get("flash_attempts", 0),
    )

    # interrupt() 在这里暂停整个 Graph；恢复值由 Runner 的
    # Command(resume={"approved": ...}) 提供，JSON 载荷可安全进 checkpoint。
    decision = interrupt(request.model_dump(mode="json"))
    approved = (
        isinstance(decision, dict)
        and bool(decision.get("approved"))
    )

    if approved:
        return {
            "approval_status": "approved",
            "approval_request": request,
            "trace": trace,
        }

    return {
        "approval_status": "rejected",
        "approval_request": request,
        "error": WorkflowError.model_validate(
            {
                "stage": "flash",
                "category": "approval_rejected",
                "message": _APPROVAL_REJECTED_MESSAGE,
                "retryable": False,
                "user_suggestion": _APPROVAL_REJECTED_SUGGESTION,
            }
        ),
        "trace": trace,
    }


def flash_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    port = runtime.context.serial_port
    if port is None:
        raise EspIdfError(
            category="serial",
            message="未配置烧录串口",
            retryable=False,
        )

    # 烧录成功与否只能由 EspIdfFlashPort 返回的真实证据决定。
    evidence = runtime.context.flasher.flash(
        runtime.context.project_path,
        port,
    )

    return {
        "flash_evidence": evidence,
        "flash_attempts": state.get("flash_attempts", 0) + 1,
        "status": "flashing",
        "trace": [
            *state.get("trace", []),
            "flash_project",
        ],
    }


def build_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    espidf = runtime.context.espidf
    project_path = runtime.context.project_path
    # 构建成功与否只能由 EspIdfPort 返回的真实证据决定，LLM 无权伪造。
    evidence = espidf.build(project_path)

    # get 在首次构建没有 attempts 键时使用 0；只有实际构建才增加次数。
    next_attempt = state.get("attempts", 0) + 1

    return {
        "build_evidence": evidence,
        "attempts": next_attempt,
        "status": "building",
        "trace": [
            *state.get("trace", []),
            "build_project",
        ],
    }


def request_clarification(
    state: WorkflowState,
) -> dict[str, object]:
    # 该节点不调用外部能力，所以不需要 Runtime 参数；当前切片在此结束工作流。
    return {
        "status": "needs_clarification",
        "trace": [
            *state.get("trace", []),
            "request_clarification",
        ],
    }


def completed(
    state: WorkflowState,
) -> dict[str, object]:
    # 终态节点只标记结果和轨迹，不再执行工具。
    return {
        "status": "completed",
        "trace": [
            *state.get("trace", []),
            "completed",
        ],
    }


def failed(
    state: WorkflowState,
) -> dict[str, object]:
    # 失败终态保留此前 State 中的最后证据，便于排查和向用户解释。
    return {
        "status": "failed",
        "trace": [
            *state.get("trace", []),
            "failed",
        ],
    }


def repair_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    project_path = runtime.context.project_path
    workspace = runtime.context.workspace
    repair_planner = runtime.context.repair_planner

    # 先由 Workspace 受控读取文件，RepairPlanner 本身没有任意磁盘访问权限。
    files = workspace.read_project_files(project_path)

    # 设备回路修复携带日志诊断；纯构建修复传 None。
    device_diagnosis = state.get("device_diagnosis")

    # 修复模型同时看到需求、原计划、失败证据和源码，返回经过验证的 RepairPlan。
    repair = repair_planner.create_repair(
        state["requirement"],
        state["plan"],
        state["build_evidence"],
        files,
        device_diagnosis=device_diagnosis,
    )

    # 只有 Workspace 能产生写文件副作用，并返回实际修改的相对路径。
    changed_files = workspace.apply_repair(
        project_path,
        repair,
    )

    # Once files change, the old snapshot is invalid. Refresh immediately so a
    # later repair/monitor step never reasons from the pre-repair code.
    refreshed = analyze_current_project(
        project_path=project_path,
        target_chip=runtime.context.target_chip,
        workspace=workspace,
        analyzer=runtime.context.project_analyzer,
        persistence=runtime.context.persistence,
        project_key=runtime.context.project_key,
        force=True,
    )

    # 记录本次修复的触发来源：路由据此决定重建成功后的去向。
    repair_origin = (
        "monitor" if device_diagnosis is not None else "build"
    )

    # 此处不返回 attempts 和 build_evidence，因此旧值会保留；下一次构建才覆盖证据。
    return {
        "repair_plan": repair,
        "changed_files": [
            *state.get("changed_files", []),
            *changed_files,
        ],
        "project_analysis": refreshed,
        "repair_origin": repair_origin,
        "status": "repaired",
        "trace": [
            *state.get("trace", []),
            "repair_project",
        ],
    }


# 设备修复回路的最大轮数；耗尽后终止，防止硬件回路无限运行。
_MAX_DEVICE_CYCLES = 3

_DEVICE_BUDGET_MESSAGE = "设备修复循环达到上限"

_DEVICE_BUDGET_SUGGESTION = "请人工检查硬件连接与固件逻辑"

_NO_REPAIR_MESSAGE = "设备运行日志异常但未发现可修复项"

_NO_REPAIR_SUGGESTION = "请人工检查设备运行日志"


def monitor_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    port = runtime.context.serial_port
    if port is None:
        raise EspIdfError(
            category="serial",
            message="未配置监控串口",
            retryable=False,
        )

    # 采集窗口由 Context 配置；超时是正常结束方式而不是失败。
    evidence = runtime.context.monitor.monitor(
        runtime.context.project_path,
        port,
        runtime.context.monitor_timeout_seconds,
    )

    return {
        "monitor_evidence": evidence,
        "status": "monitoring",
        "trace": [
            *state.get("trace", []),
            "monitor_project",
        ],
    }


def analyze_device_logs(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    # 只有 LogAnalystPort 能把日志变成诊断；LLM 无权直接宣称设备健康。
    diagnosis = runtime.context.log_analyst.analyze(
        state["requirement"],
        state["monitor_evidence"],
    )
    cycles = state.get("device_cycles", 0) + 1

    update: dict[str, object] = {
        "device_diagnosis": diagnosis,
        "device_cycles": cycles,
        "status": "diagnosed",
        "trace": [
            *state.get("trace", []),
            "analyze_device_logs",
        ],
    }

    # 需要修复但预算耗尽：以固定脱敏错误终止。
    if diagnosis.repair_needed and cycles > _MAX_DEVICE_CYCLES:
        update["error"] = WorkflowError.model_validate(
            {
                "stage": "monitor",
                "category": "unknown",
                "message": _DEVICE_BUDGET_MESSAGE,
                "retryable": False,
                "user_suggestion": _DEVICE_BUDGET_SUGGESTION,
            }
        )

    # 不健康但无修复建议：同样终止，避免虚假完成。
    if not diagnosis.healthy and not diagnosis.repair_needed:
        update["error"] = WorkflowError.model_validate(
            {
                "stage": "monitor",
                "category": "unknown",
                "message": _NO_REPAIR_MESSAGE,
                "retryable": False,
                "user_suggestion": _NO_REPAIR_SUGGESTION,
            }
        )

    return update
