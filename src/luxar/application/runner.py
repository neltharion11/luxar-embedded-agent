"""工作流运行边界：统一执行 Graph，并把模型能力异常转换成失败 State。"""

from __future__ import annotations

from typing import cast

from luxar.application.context import RuntimeContext
from luxar.application.graph import build_graph
from luxar.application.nodes import failed
from luxar.application.state import WorkflowState
from luxar.domain.errors import WorkflowError
from luxar.ports.errors import CapabilityError


# 将底层能力错误分类翻译成工作流能够保存的业务分类。
CAPABILITY_CATEGORY_MAP = {
    "authentication": "authentication",
    "timeout": "timeout",
    "rate_limit": "rate_limit",
    "service": "service",
    "empty_response": "model_output",
    "invalid_json": "model_output",
    "invalid_schema": "model_output",
}


# 这些文字完全由应用控制，不使用异常原始消息。
# 即使底层异常意外包含密钥或服务器响应，也不会进入 State。
WORKFLOW_ERROR_MESSAGES = {
    "authentication": "模型服务认证失败",
    "timeout": "模型服务请求超时",
    "rate_limit": "模型服务请求受到限流",
    "service": "模型服务暂时不可用",
    "model_output": "模型返回的数据不符合工作流要求",
}


WORKFLOW_ERROR_SUGGESTIONS = {
    "authentication": "请检查模型服务的 API 密钥配置",
    "timeout": "请稍后重试或检查网络连接",
    "rate_limit": "请等待限流恢复后重试",
    "service": "请稍后重试并检查模型服务状态",
    "model_output": "请重试；如果持续失败，请检查模型和提示词配置",
}


def capability_error_to_workflow_error(
    error: CapabilityError,
    state: WorkflowState,
) -> WorkflowError:
    # 根据已经成功写入 State 的数据，判断失败发生在哪个模型阶段。
    if "requirement" not in state:
        stage = "requirement_analysis"
    elif "plan" not in state:
        stage = "planning"
    else:
        stage = "repair"

    category = CAPABILITY_CATEGORY_MAP[error.category]

    # model_validate 接收普通字典，并由 Pydantic 验证最终领域对象。
    return WorkflowError.model_validate(
        {
            "stage": stage,
            "category": category,
            "message": WORKFLOW_ERROR_MESSAGES[category],
            "retryable": error.retryable,
            "user_suggestion": WORKFLOW_ERROR_SUGGESTIONS[category],
        }
    )


def run_workflow(
    *,
    initial_state: WorkflowState,
    context: RuntimeContext,
) -> WorkflowState:
    # 先复制初始 State。即使第一个节点立刻失败，task_text 也不会丢失。
    latest_state = cast(
        WorkflowState,
        dict(initial_state),
    )

    try:
        # stream_mode="values" 会在每个节点成功后给出完整 State 快照。
        for snapshot in build_graph().stream(
            initial_state,
            context=context,
            stream_mode="values",
        ):
            latest_state = cast(
                WorkflowState,
                snapshot,
            )

    # 整个工作流只有这一处统一捕获 CapabilityError。
    except CapabilityError as error:
        workflow_error = capability_error_to_workflow_error(
            error,
            latest_state,
        )

        # 复用已有 failed 节点的状态和 trace 更新逻辑。
        failure_update = failed(latest_state)

        return cast(
            WorkflowState,
            {
                **latest_state,
                "error": workflow_error,
                **failure_update,
            },
        )

    return latest_state