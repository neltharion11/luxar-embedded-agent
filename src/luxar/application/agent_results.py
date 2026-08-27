"""Supervisor State 的 CLI/Web 安全展示合同。"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from luxar.application.agent_state import AgentState
from luxar.domain.agent.code_changes import ChangeBundle, ChangeBundleValidation
from luxar.domain.agent.tasks import AgentTaskGraph
from luxar.domain.agent.changes import ObjectiveInterpretation
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.devices import FlashEvidence, MonitorEvidence
from luxar.domain.evidence import BuildEvidence


def _json_model(value: BaseModel | None) -> dict[str, object] | None:
    return value.model_dump(mode="json") if value is not None else None


def _failure_context(state: AgentState) -> dict[str, object] | None:
    """给上层持续 Agent 的有界失败事实，而不是预先替模型下结论。"""

    history: list[dict[str, object]] = []
    for raw_failure in state.get("failure_history", [])[-3:]:
        if isinstance(raw_failure, BaseModel):
            failure = raw_failure.model_dump(mode="json")
        elif isinstance(raw_failure, Mapping):
            failure = dict(raw_failure)
        else:
            continue
        history.append(
            {
                key: failure[key]
                for key in (
                    "task_id",
                    "message",
                    "errors",
                    "attempt",
                    "repeated",
                )
                if key in failure
            }
        )
    current_task_id = state.get("current_task_id")
    feedback = (
        list(state.get("task_feedback", {}).get(current_task_id, []))[-20:]
        if current_task_id is not None
        else []
    )
    recovery = state.get("build_recovery")
    if isinstance(recovery, BaseModel):
        recovery_payload = recovery.model_dump(mode="json")
    elif isinstance(recovery, Mapping):
        recovery_payload = dict(recovery)
    else:
        recovery_payload = None
    if not history and not feedback and recovery_payload is None:
        return None
    return {
        "current_task_id": current_task_id,
        "recent_failures": history,
        "task_feedback": feedback,
        "build_recovery": recovery_payload,
    }


def agent_exit_code_for_state(state: AgentState) -> int:
    return {
        "completed": 0,
        "awaiting_user": 3,
        "blocked": 4,
        "failed": 4,
    }.get(state.get("status"), 4)


def agent_state_to_result(state: AgentState) -> dict[str, object]:
    graph = state.get("task_graph")
    if isinstance(graph, dict):
        graph = AgentTaskGraph.model_validate(graph)
    objective = state.get("objective")
    if isinstance(objective, dict):
        objective = ProjectObjective.model_validate(objective)
    build = state.get("build_evidence")
    if isinstance(build, dict):
        build = BuildEvidence.model_validate(build)
    flash = state.get("flash_evidence")
    if isinstance(flash, dict):
        flash = FlashEvidence.model_validate(flash)
    monitor = state.get("monitor_evidence")
    if isinstance(monitor, dict):
        monitor = MonitorEvidence.model_validate(monitor)
    validations = [
        value
        if isinstance(value, ChangeBundleValidation)
        else ChangeBundleValidation.model_validate(value)
        for value in state.get("change_validations", {}).values()
    ]
    changed_files = sorted(
        {
            path
            for validation in validations
            for path in validation.changed_files
        }
    )
    return {
        "status": state.get("status", "failed"),
        "objective": _json_model(objective),
        "task_graph": _json_model(graph),
        "changed_files": changed_files,
        "changes": [
            (
                item.model_dump(mode="json")
                if isinstance(item, BaseModel)
                else dict(item)
            )
            for item in state.get("applied_changes", [])[:100]
            if isinstance(item, (BaseModel, Mapping))
        ],
        "evidence_ids": list(state.get("evidence_ids", [])),
        "build_evidence": _json_model(build),
        "flash_evidence": _json_model(flash),
        "monitor_evidence": _json_model(monitor),
        "acceptance_passed": bool(state.get("acceptance_passed", False)),
        "build_verified": bool(state.get("build_verified", False)),
        "hardware_function_verified": bool(
            state.get("hardware_function_verified", False)
        ),
        "approval_status": state.get("approval_status", "not_requested"),
        "last_error": state.get("last_error") or None,
        "failure_context": _failure_context(state),
    }


def _objective_text(state: AgentState) -> str:
    objective = state.get("objective")
    if isinstance(objective, dict):
        objective = ProjectObjective.model_validate(objective)
    if isinstance(objective, ProjectObjective):
        return objective.title or objective.description
    task_text = str(state.get("task_text", "")).strip()
    return task_text or "当前项目任务"


def _change_details(state: AgentState) -> list[str]:
    details: list[str] = []
    validations = state.get("change_validations", {})
    if isinstance(validations, Mapping):
        for raw_validation in validations.values():
            validation = (
                raw_validation
                if isinstance(raw_validation, ChangeBundleValidation)
                else ChangeBundleValidation.model_validate(raw_validation)
            )
            details.extend(validation.diff_summary[:8])

    bundles = state.get("change_bundles", {})
    if isinstance(bundles, Mapping):
        for raw_bundle in bundles.values():
            bundle = (
                raw_bundle
                if isinstance(raw_bundle, ChangeBundle)
                else ChangeBundle.model_validate(raw_bundle)
            )
            description = bundle.description.strip()
            if description:
                details.append(f"修改目的：{description[:240]}")
            details.extend(
                f"{change.operation}: {change.path}" for change in bundle.changes[:8]
            )
    if details:
        return list(dict.fromkeys(details))[:12]

    changed = agent_state_to_result(state)["changed_files"]
    if isinstance(changed, list):
        return [f"modify: {path}" for path in changed[:12]]
    return []


def _plan_text(state: AgentState) -> str:
    """把内部任务图压缩成适合对话窗口的一句话计划。"""

    graph = state.get("task_graph")
    if isinstance(graph, dict):
        graph = AgentTaskGraph.model_validate(graph)
    if not isinstance(graph, AgentTaskGraph) or not graph.tasks:
        return "检查现有工程，完成所需修改，并验证目标和非回归条件"
    titles = [task.title.strip() for task in graph.tasks if task.title.strip()]
    return "；".join(titles[:8])


def _diagnosis_text(state: AgentState) -> str:
    failures = state.get("failure_history", [])
    failure_messages: list[str] = []
    if isinstance(failures, list):
        for raw_failure in failures[-3:]:
            if isinstance(raw_failure, Mapping):
                message = str(raw_failure.get("message", "")).strip()
            elif isinstance(raw_failure, BaseModel):
                message = str(getattr(raw_failure, "message", "")).strip()
            else:
                message = ""
            if message:
                failure_messages.append(message)
    if failure_messages:
        return "；".join(dict.fromkeys(failure_messages))

    build = state.get("build_evidence")
    if isinstance(build, dict):
        build = BuildEvidence.model_validate(build)
    if isinstance(build, BuildEvidence) and not build.success:
        diagnostics = build.diagnostics[:4]
        if diagnostics:
            rendered: list[str] = []
            for diagnostic in diagnostics:
                location = diagnostic.file or "构建输出"
                if diagnostic.line is not None:
                    location += f":{diagnostic.line}"
                rendered.append(f"{location}：{diagnostic.message}")
            return "构建失败，具体诊断为：" + "；".join(rendered)
        if build.stderr_summary.strip():
            return "构建失败：" + build.stderr_summary.strip()[:600]

    task_text = f"{state.get('task_text', '')} {_objective_text(state)}".casefold()
    if any(word in task_text for word in ("屏幕", "显示", "oled", "ssd1306", "screen", "display")):
        if not state.get("hardware_function_verified", False):
            return (
                "目前不能从现有证据确认单一根因；优先排查供电、SDA/SCL 接线、"
                "I2C 地址、初始化时序和设备运行日志"
            )
    last_error = str(state.get("last_error", "")).strip()
    return last_error or "未发现可由当前工具证据确认的错误原因"


def _device_verification_expected(state: AgentState) -> bool:
    plan = state.get("verification_plan")
    if isinstance(plan, Mapping):
        if plan.get("require_device"):
            return True
    elif isinstance(plan, BaseModel) and getattr(plan, "require_device", False):
        return True
    task_text = f"{state.get('task_text', '')} {_objective_text(state)}".casefold()
    return any(
        word in task_text
        for word in ("屏幕", "显示", "oled", "ssd1306", "screen", "display")
    )


def _verification_text(state: AgentState) -> str:
    checks: list[str] = []
    build = state.get("build_evidence")
    if isinstance(build, dict):
        build = BuildEvidence.model_validate(build)
    if isinstance(build, BuildEvidence):
        checks.append("构建验证通过" if build.success else "构建验证失败")
    elif state.get("build_verified"):
        checks.append("构建验证通过")

    flash = state.get("flash_evidence")
    if isinstance(flash, dict):
        flash = FlashEvidence.model_validate(flash)
    if isinstance(flash, FlashEvidence):
        checks.append("烧录验证通过" if flash.success else "烧录验证失败")

    if state.get("hardware_function_verified"):
        checks.append("设备功能已验证")
    elif _device_verification_expected(state):
        checks.append("设备功能未验证")

    criteria = state.get("acceptance_criteria", [])
    if isinstance(criteria, list) and criteria:
        passed = 0
        for raw_criterion in criteria:
            status = (
                raw_criterion.status
                if isinstance(raw_criterion, BaseModel)
                else raw_criterion.get("status")
                if isinstance(raw_criterion, Mapping)
                else None
            )
            passed += status == "passed"
        checks.append(f"验收条件 {passed}/{len(criteria)} 项通过")
    return "；".join(checks)


def _completed_message(state: AgentState) -> str:
    changed = _change_details(state)
    changes = (
        "本次修改：\n" + "\n".join(f"- {item}" for item in changed)
        if changed
        else "本次没有记录到源码修改。"
    )
    completion_scope = (
        ""
        if state.get("hardware_function_verified")
        or not _device_verification_expected(state)
        else "（源码与构建部分）"
    )
    return "\n\n".join(
        [
            f"目标：{_objective_text(state)}。",
            f"计划：{_plan_text(state)}。",
            f"完成情况：项目目标已完成{completion_scope}。",
            f"问题判断：{_diagnosis_text(state)}。",
            changes,
            f"验证结果：{_verification_text(state)}。",
        ]
    )


def agent_user_message_for_state(state: AgentState) -> str:
    status = state.get("status", "failed")
    if status == "completed":
        return _completed_message(state)
    if status == "awaiting_user":
        interpretation = state.get("interpretation")
        if isinstance(interpretation, dict):
            interpretation = ObjectiveInterpretation.model_validate(
                interpretation
            )
        questions = interpretation.questions if interpretation is not None else []
        return questions[0] if questions else "需要补充工程目标或约束。"
    if status == "blocked":
        return "项目目标已阻塞：" + state.get("last_error", "需要人工处理")
    return "项目目标执行失败：" + state.get(
        "last_error", "请检查运行配置后重试"
    )


__all__ = [
    "agent_exit_code_for_state",
    "agent_state_to_result",
    "agent_user_message_for_state",
]
