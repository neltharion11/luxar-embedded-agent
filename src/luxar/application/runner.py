"""工作流运行边界：统一执行 Graph，并把能力异常转换成失败 State。"""

from __future__ import annotations

from typing import cast

from luxar.application.context import RuntimeContext
from luxar.application.graph import build_graph
from luxar.application.nodes import failed
from luxar.application.state import WorkflowState
from luxar.domain.errors import WorkflowError
from luxar.ports.errors import CapabilityError
from luxar.ports.workspace_errors import WorkspaceError


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


WORKSPACE_ERROR_MESSAGES = {
    "invalid_project": "项目目录或修复目标无效",
    "unsafe_path": "工作区拒绝了不安全的文件路径",
    "unsupported_file": "修复计划包含不允许修改的文件",
    "file_too_large": "项目文件超过工作区处理上限",
    "context_too_large": "项目源码总量超过工作区处理上限",
    "invalid_encoding": "项目源码不是有效的 UTF-8 文本",
    "io": "工作区文件操作失败",
    "rollback_failed": "工作区修复失败且未能完整回滚",
}


WORKSPACE_ERROR_SUGGESTIONS = {
    "invalid_project": "请检查项目目录和修复目标是否存在",
    "unsafe_path": "请检查修复计划中的项目相对路径",
    "unsupported_file": "请仅修改允许的 ESP-IDF 项目源码",
    "file_too_large": "请缩小单个源码文件或调整工作区限制",
    "context_too_large": "请缩小本次修复涉及的源码范围",
    "invalid_encoding": "请将项目源码转换为 UTF-8 文本",
    "io": "请检查文件权限后重试",
    "rollback_failed": "请停止自动修复并人工检查工作区文件",
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


def workspace_error_to_workflow_error(
    error: WorkspaceError,
) -> WorkflowError:
    return WorkflowError.model_validate(
        {
            "stage": "repair",
            "category": "workspace",
            "message": WORKSPACE_ERROR_MESSAGES[
                error.category
            ],
            "retryable": error.retryable,
            "user_suggestion": WORKSPACE_ERROR_SUGGESTIONS[
                error.category
            ],
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

    # 整个工作流只有这一处统一捕获模型和工作区能力异常。
    except (CapabilityError, WorkspaceError) as error:
        if isinstance(error, CapabilityError):
            workflow_error = capability_error_to_workflow_error(
                error,
                latest_state,
            )
        else:
            workflow_error = workspace_error_to_workflow_error(
                error
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
