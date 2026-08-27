"""工作流结果展示合同：为 CLI 和 Web 提供同一份安全白名单结果。"""

from __future__ import annotations

import re

from pydantic import BaseModel

from luxar.application.state import WorkflowState


def exit_code_for_state(state: WorkflowState) -> int:
    """把三个业务终态映射成稳定的进程/接口结果码。"""

    return {
        "completed": 0,
        "needs_clarification": 3,
        "failed": 4,
    }.get(state.get("status"), 4)


def _serialize_model(value: BaseModel | None) -> dict[str, object] | None:
    if value is None:
        return None
    return value.model_dump(mode="json")


_MISSING_FIELD_LABELS = {
    "target": "目标芯片",
    "goal": "项目需要实现的功能",
}

_PERIPHERAL_FIELD = re.compile(r"^peripherals\[(\d+)]\.(.+)$")

_PLAN_STEP_LABELS = {
    "create_project": "创建基础工程",
    "implement_change": "根据当前代码实现需求",
    "build_project": "构建并验证固件",
    "flash_project": "烧录固件",
    "monitor_project": "采集并分析设备日志",
}


def _missing_field_label(field: str, requirement: object) -> str:
    direct = _MISSING_FIELD_LABELS.get(field)
    if direct is not None:
        return direct
    match = _PERIPHERAL_FIELD.fullmatch(field)
    peripherals = getattr(requirement, "peripherals", [])
    if match is not None:
        index = int(match.group(1))
        if index < len(peripherals):
            kind = peripherals[index].kind.upper()
            return f"{kind} 外设的 {match.group(2)} 参数"
    return field


def _join_chinese_sentences(items: list[str]) -> str:
    cleaned = [item.rstrip("。；;，, ") for item in items if item.strip()]
    return "；".join(cleaned) + "。" if cleaned else ""


def _completed_message(state: WorkflowState) -> str:
    """Build one self-contained engineering report for the completed run."""

    knowledge_result = state.get("knowledge_result")
    if knowledge_result is not None:
        grounded_answer = str(knowledge_result.get("answer", "")).strip()
        if grounded_answer and not knowledge_result.get("read_pdf"):
            return grounded_answer
        if knowledge_result.get("read_pdf"):
            preview = str(knowledge_result.get("preview", "")).strip()
            answer = str(knowledge_result.get("answer", "")).strip()
            message = (
                f"PDF 已完整分批读取：共 {knowledge_result.get('total_pages', 0)} 页，"
                f"划分为 {knowledge_result.get('sections', knowledge_result.get('batches', 0))} 个章节单元，"
                f"提取约 {knowledge_result.get('characters', 0)} 个字符。"
            )
            warning = str(knowledge_result.get("analysis_warning", "")).strip()
            if warning:
                message += "\n\n" + warning
            if answer:
                return message + "\n\n" + answer
            return message + ("\n\n读取内容预览\n\n" + preview if preview else "")
        if "documents" in knowledge_result:
            count = len(knowledge_result.get("documents", []))
            return f"知识库查询完成，当前共有 {count} 份文档。"
        if "matches" in knowledge_result:
            matches = knowledge_result.get("matches", [])
            titles = [str(item.get("title", "")) for item in matches[:6] if isinstance(item, dict)]
            return "知识检索完成，共找到 {} 条匹配{}。".format(
                len(matches), "：" + "、".join(titles) if titles else ""
            )
        if "deleted" in knowledge_result:
            return "知识文档已删除。" if knowledge_result["deleted"] else "未找到要删除的知识文档。"
        if "total_pages" in knowledge_result:
            return (
                f"PDF 已完整分批读取并写入知识库：共 {knowledge_result['total_pages']} 页，"
                f"划分为 {knowledge_result.get('sections', knowledge_result.get('batches', 0))} 个章节单元，"
                f"抽取并写入 {knowledge_result.get('knowledge_units', 0)} 条具体知识。"
            )
        return f"知识文档已写入，共生成 {knowledge_result.get('chunks', 0)} 个检索分块。"

    lines = ["处理完成。"]
    analysis = state.get("project_analysis")
    requirement = state.get("requirement")
    if analysis is not None:
        lines.extend(["", "项目判断", f"- {analysis.summary}"])
        if analysis.gaps and (
            requirement is None or requirement.project_type != "empty"
        ):
            lines.append(
                "- 尚未完成：" + _join_chinese_sentences(analysis.gaps[:3])
            )

    plan = state.get("plan")
    if plan is not None:
        labels = [
            _PLAN_STEP_LABELS.get(step.kind, step.description)
            for step in plan.steps
        ]
        lines.extend(["", "执行内容", "- " + " → ".join(labels)])

    changed_files = list(dict.fromkeys(state.get("changed_files", [])))
    references = state.get("reference_examples", [])
    if references:
        lines.extend(
            [
                "",
                "复用依据",
                "- ESP-IDF 官方例程："
                + "、".join(item.path for item in references),
            ]
        )
    created = state.get("created_project")
    if analysis is not None or plan is not None or created is not None or changed_files:
        lines.extend(["", "代码改动"])
        if changed_files:
            lines.extend(f"- {path}" for path in changed_files)
        elif created is not None and created.success and not created.already_existed:
            lines.append("- 已创建基础 ESP-IDF 工程结构。")
            if analysis is not None and analysis.evidence_paths:
                lines.append(
                    "- 当前工程文件："
                    + "、".join(analysis.evidence_paths[:12])
                    + "。"
                )
        else:
            if requirement is not None and requirement.project_type == "empty":
                lines.append(
                    "- 本次未修改源码：现有项目已经是基础空框架；"
                    "继续加入业务代码反而会偏离本次需求。"
                )
            else:
                lines.append(
                    "- 本次未修改源码；当前项目已具备计划所需结构，"
                    "本轮只执行验证步骤。"
                )

    verification: list[str] = []
    build = state.get("build_evidence")
    if build is not None and build.success:
        verification.append(
            f"构建通过：共执行 {state.get('attempts', 0)} 次，"
            f"最终返回码 {build.return_code}。"
        )
    flash = state.get("flash_evidence")
    if flash is not None and flash.success:
        verification.append(f"固件已烧录到 {flash.port}。")
    diagnosis = state.get("device_diagnosis")
    if diagnosis is not None and diagnosis.healthy:
        verification.append("设备日志检查正常，未发现需要继续修复的问题。")
    if verification:
        lines.extend(["", "验证结果"])
        lines.extend(f"- {item}" for item in verification)

    return "\n".join(lines)


def user_message_for_state(state: WorkflowState) -> str:
    """Turn internal workflow state into a concise user-facing response."""

    inspection_response = state.get("inspection_response")
    if inspection_response:
        return inspection_response

    status = state.get("status", "failed")
    if status == "needs_clarification":
        requirement = state.get("requirement")
        missing = requirement.blocking_missing_fields if requirement else []
        labels = [
            _missing_field_label(item, requirement) for item in missing
        ]
        if labels:
            requested = "、".join(labels)
            return (
                f"还需要你补充：{requested}。"
                "如果只需要基础空项目，请直接回复“创建基础空项目”。"
            )
        return "还需要一些需求信息，请说明希望项目实现什么功能。"

    if status == "completed":
        return _completed_message(state)

    error = state.get("error")
    if error is not None:
        return f"任务执行失败：{error.message}。建议：{error.user_suggestion}。"
    return "任务执行失败，请检查项目配置后重试。"


def live_message_for_state(state: WorkflowState) -> str:
    """Build the final sentence for an incremental Web conversation.

    ``user_message_for_state`` remains the complete, self-contained report used
    by persistence and non-streaming clients.  The Web stream has already told
    the user what was inspected and executed, so repeating that report would
    make the conversation sound like several independent agents taking turns.
    """

    if state.get("status") != "completed" or state.get("inspection_response"):
        return user_message_for_state(state)

    # Knowledge operations do not emit the firmware workflow's incremental
    # analysis/code/build narrative.  Their final result is therefore the
    # actual user-visible content, not a generic "no source changed" summary.
    if state.get("knowledge_result") is not None:
        return user_message_for_state(state)

    changed_files = list(dict.fromkeys(state.get("changed_files", [])))
    created = state.get("created_project")
    requirement = state.get("requirement")
    build = state.get("build_evidence")
    flash = state.get("flash_evidence")
    diagnosis = state.get("device_diagnosis")

    conclusions: list[str] = []
    if changed_files:
        conclusions.append("已修改 " + "、".join(changed_files[:8]))
    elif created is not None and created.success and not created.already_existed:
        conclusions.append("基础工程已经创建")
    elif requirement is not None and requirement.project_type == "empty":
        conclusions.append(
            "现有项目已经是符合需求的空框架，因此没有修改源码"
        )
    else:
        conclusions.append("本次没有修改源码")

    if build is not None and build.success:
        conclusions.append(
            f"构建验证通过，最终返回码为 {build.return_code}"
        )
    if flash is not None and flash.success:
        conclusions.append(f"固件已经烧录到 {flash.port}")
    if diagnosis is not None and diagnosis.healthy:
        conclusions.append("设备运行日志未发现故障")

    return "处理完成。" + _join_chinese_sentences(conclusions)


def state_to_result(state: WorkflowState) -> dict[str, object]:
    """只选择允许离开应用边界的字段，不序列化整个 State。

    approval_request、task_text 与原始日志永远不进入本白名单。
    """

    result: dict[str, object] = {
        "status": state.get("status", "failed"),
        "message": user_message_for_state(state),
        "exit_code": exit_code_for_state(state),
        "attempts": state.get("attempts", 0),
        "requirement": _serialize_model(state.get("requirement")),
        "project_analysis": _serialize_model(
            state.get("project_analysis")
        ),
        "reference_examples": [
            {
                "path": item.path,
                "score": item.score,
                "matched_terms": list(item.matched_terms),
                "summary": item.summary,
            }
            for item in state.get("reference_examples", [])
        ],
        "plan": _serialize_model(state.get("plan")),
        "created_project": _serialize_model(
            state.get("created_project")
        ),
        "build_evidence": _serialize_model(state.get("build_evidence")),
        "flash_evidence": _serialize_model(state.get("flash_evidence")),
        "monitor_evidence": _serialize_model(
            state.get("monitor_evidence")
        ),
        "device_diagnosis": _serialize_model(
            state.get("device_diagnosis")
        ),
        "approval_status": state.get(
            "approval_status",
            "not_requested",
        ),
        "repair_plan": _serialize_model(state.get("repair_plan")),
        "implementation_plan": _serialize_model(
            state.get("implementation_plan")
        ),
        "changed_files": list(state.get("changed_files", [])),
        "error": _serialize_model(state.get("error")),
        "trace": list(state.get("trace", [])),
    }
    if "knowledge_task" in state:
        result["knowledge_task"] = _serialize_model(state.get("knowledge_task"))
    if "knowledge_result" in state:
        result["knowledge_result"] = state.get("knowledge_result")
    return result
