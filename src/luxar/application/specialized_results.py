"""Safe result contract for dedicated inspection and knowledge workflows."""

from __future__ import annotations

from pydantic import BaseModel

from luxar.application.specialized_state import SpecializedWorkflowState


def _serialize(value: BaseModel | None) -> dict[str, object] | None:
    return value.model_dump(mode="json") if value is not None else None


def _knowledge_message(result: dict[str, object]) -> str:
    answer = str(result.get("answer", "")).strip()
    if answer and not result.get("read_pdf"):
        citations = result.get("citations", [])
        source_lines: list[str] = []
        if isinstance(citations, list):
            for item in citations:
                if not isinstance(item, dict):
                    continue
                pages = item.get("source_pages", [])
                page_text = (
                    "，第 " + "、".join(str(page) for page in pages) + " 页"
                    if isinstance(pages, list) and pages
                    else ""
                )
                source_lines.append(
                    f"- [{item.get('evidence_id', '')}] "
                    f"{item.get('title', '')}{page_text}"
                )
        return answer + (
            "\n\n资料来源\n\n" + "\n".join(source_lines)
            if source_lines
            else ""
        )
    if result.get("read_pdf"):
        preview = str(result.get("preview", "")).strip()
        answer = str(result.get("answer", "")).strip()
        message = (
            f"PDF 已完整分批读取：共 {result.get('total_pages', 0)} 页，"
            f"划分为 {result.get('sections', result.get('batches', 0))} 个章节单元，"
            f"提取约 {result.get('characters', 0)} 个字符。"
        )
        warning = str(result.get("analysis_warning", "")).strip()
        if warning:
            message += "\n\n" + warning
        if answer:
            return message + "\n\n" + answer
        return message + ("\n\n读取内容预览\n\n" + preview if preview else "")
    if "documents" in result:
        return f"知识库查询完成，当前共有 {len(result.get('documents', []))} 份文档。"
    if "matches" in result:
        matches = result.get("matches", [])
        items = matches if isinstance(matches, list) else []
        titles = [
            str(item.get("title", ""))
            for item in items[:6]
            if isinstance(item, dict)
        ]
        return "知识检索完成，共找到 {} 条匹配{}。".format(
            len(items),
            "：" + "、".join(titles) if titles else "",
        )
    if "deleted" in result:
        return (
            "知识文档已删除。"
            if result["deleted"]
            else "未找到要删除的知识文档。"
        )
    if "total_pages" in result:
        return (
            f"PDF 已完整分批读取并写入知识库：共 {result['total_pages']} 页，"
            f"划分为 {result.get('sections', result.get('batches', 0))} 个章节单元，"
            f"抽取并写入 {result.get('knowledge_units', 0)} 条具体知识。"
        )
    return f"知识文档已写入，共生成 {result.get('chunks', 0)} 个检索分块。"


def specialized_user_message(
    state: SpecializedWorkflowState,
) -> str:
    inspection = state.get("inspection_response")
    if inspection:
        return inspection
    if state.get("status") == "completed":
        result = state.get("knowledge_result")
        message = _knowledge_message(result) if result is not None else "处理完成。"
        if state.get("knowledge_retrieval_selected"):
            reason = state.get("knowledge_retrieval_reason", "").strip()
            return (
                "检索说明：源码分析后决定检索项目知识库。"
                + (reason if reason else "知识资料可能补齐代码之外的关键证据。")
                + "\n\n"
                + message
            )
        return message
    error = state.get("error")
    if error is not None:
        return f"任务执行失败：{error.message}。建议：{error.user_suggestion}。"
    return "任务执行失败，请检查项目或知识服务配置后重试。"


def specialized_state_to_result(
    state: SpecializedWorkflowState,
) -> dict[str, object]:
    status = state.get("status", "failed")
    result: dict[str, object] = {
        "status": status,
        "message": specialized_user_message(state),
        "exit_code": 0 if status == "completed" else 4,
        "project_analysis": _serialize(state.get("project_analysis")),
        "error": _serialize(state.get("error")),
        "trace": list(state.get("trace", [])),
    }
    if "knowledge_task" in state:
        result["knowledge_task"] = _serialize(state.get("knowledge_task"))
    if "knowledge_result" in state:
        result["knowledge_result"] = state.get("knowledge_result")
    if "evidence_assessment" in state:
        result["evidence_assessment"] = state.get("evidence_assessment")
    if "answer_verification" in state:
        result["answer_verification"] = state.get("answer_verification")
    return result


__all__ = ["specialized_state_to_result", "specialized_user_message"]
