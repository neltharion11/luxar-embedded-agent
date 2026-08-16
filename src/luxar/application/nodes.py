"""LangGraph 业务节点：通过 Runtime 中的 Ports 执行动作，并返回最小 State 更新。"""

from __future__ import annotations

from langgraph.runtime import Runtime

from luxar.application.context import RuntimeContext
from luxar.application.state import WorkflowState
from luxar.domain.errors import WorkflowError


# 已实现步骤词表随切片扩展；未进入本表的步骤会被分发器拒绝。
_SUPPORTED_STEP_KINDS = frozenset(
    {
        "create_project",
        "build_project",
    }
)

_UNSUPPORTED_STEP_MESSAGE = "执行计划包含当前版本不支持的步骤"

_UNSUPPORTED_STEP_SUGGESTION = "请更换模型或升级 LUXAR 后重试"


def analyze_requirement(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    # runtime.context 由 LangGraph 在调用节点时注入；具体对象可以是 Fake 或 DeepSeek Adapter。
    requirement = runtime.context.requirement_parser.parse(
        state["task_text"]
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
    plan = planner.create_plan(requirement)

    return {
        "plan": plan,
        "status": "planned",
        "trace": [
            *state.get("trace", []),
            "create_plan",
        ],
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

    # 修复模型同时看到需求、原计划、失败证据和源码，返回经过验证的 RepairPlan。
    repair = repair_planner.create_repair(
        state["requirement"],
        state["plan"],
        state["build_evidence"],
        files,
    )

    # 只有 Workspace 能产生写文件副作用，并返回实际修改的相对路径。
    changed_files = workspace.apply_repair(
        project_path,
        repair,
    )

    # 此处不返回 attempts 和 build_evidence，因此旧值会保留；下一次构建才覆盖证据。
    return {
        "repair_plan": repair,
        "changed_files": changed_files,
        "status": "repaired",
        "trace": [
            *state.get("trace", []),
            "repair_project",
        ],
    }
