"""Conversation routing values used before the firmware workflow."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_FIRMWARE_ACTION_MARKERS = (
    "烧录",
    "烧写",
    "刷写",
    "刷入固件",
    "上传固件",
    "flash",
    "upload firmware",
)
_FIRMWARE_QUESTION_MARKERS = (
    "吗",
    "是否",
    "有没有",
    "怎么",
    "如何",
    "为什么",
    "失败",
    "报错",
    "错误",
    "原因",
    "成功",
    "不要烧录",
    "不烧录",
    "无需烧录",
    "do not flash",
    "don't flash",
)
_DISPLAY_MARKERS = (
    "屏幕",
    "显示",
    "oled",
    "ssd1306",
    "sh1106",
    "screen",
    "display",
)
_DISPLAY_SYMPTOM_MARKERS = (
    "不亮",
    "没亮",
    "未亮",
    "无显示",
    "没有显示",
    "不显示",
    "显示不出来",
    "黑屏",
    "无反应",
    "没有反应",
    "没有任何反应",
    "没反应",
    "不工作",
    "blank",
    "blank screen",
    "no display",
    "not lit",
    "no response",
    "doesn't work",
    "does not work",
)
_DISPLAY_MODIFICATION_MARKERS = (
    "修复",
    "修改",
    "改代码",
    "调整",
    "替换",
    "实现",
    "编写",
    "增加",
    "新增",
    "fix",
    "modify",
    "implement",
    "repair",
)

_PDF_READ_MARKERS = (
    "读取",
    "阅读",
    "读一下",
    "打开",
    "查看",
    "检查",
    "分析",
    "理解",
    "read",
    "open",
    "inspect",
    "analyze",
    "review",
)
_PDF_READ_NEGATIONS = (
    "不要读取",
    "不用读取",
    "无需读取",
    "别读取",
    "不要打开",
    "do not read",
    "don't read",
)
_PDF_IMPORT_MARKERS = (
    "导入",
    "加入知识库",
    "写入知识库",
    "保存到知识库",
    "import",
)
_QUOTED_PDF_PATH = re.compile(
    r'''(?P<quote>["'])(?P<path>(?:[a-zA-Z]:[\\/]|/)[^"'\r\n]+?\.pdf)(?P=quote)''',
    re.IGNORECASE,
)
_WINDOWS_PDF_PATH = re.compile(
    r"(?<![\w])(?P<path>[a-zA-Z]:[\\/][^\r\n]*?\.pdf)(?=$|\s)",
    re.IGNORECASE,
)
_POSIX_PDF_PATH = re.compile(
    r"(?<![\w])(?P<path>/[^\r\n]*?\.pdf)(?=$|\s)",
    re.IGNORECASE,
)


def explicit_pdf_read_path(message: str) -> str | None:
    """Return a user-authored absolute PDF path for an explicit read request.

    This is a high-confidence routing guard, not a general intent classifier.
    Mutating imports and negated requests remain model-routed.
    """

    text = message.strip()
    lowered = text.casefold()
    if (
        not any(marker in lowered for marker in _PDF_READ_MARKERS)
        or any(marker in lowered for marker in _PDF_READ_NEGATIONS)
        or any(marker in lowered for marker in _PDF_IMPORT_MARKERS)
    ):
        return None
    for pattern in (_QUOTED_PDF_PATH, _WINDOWS_PDF_PATH, _POSIX_PDF_PATH):
        match = pattern.search(text)
        if match is not None:
            return match.group("path")
    return None


def is_explicit_pdf_read_request(message: str) -> bool:
    """Recognize a concrete, read-only PDF command without model judgment."""

    return explicit_pdf_read_path(message) is not None


def is_explicit_firmware_command(message: str) -> bool:
    """识别高置信度烧录命令，避免外部动作被模型误判为知识问题。"""

    text = message.strip().casefold()
    question_only = text.endswith(("?", "？")) and not any(
        marker in text for marker in ("请", "现在", "我要", "帮我", "直接", "立即", "让你")
    )
    return bool(
        text
        and any(marker in text for marker in _FIRMWARE_ACTION_MARKERS)
        and not any(marker in text for marker in _FIRMWARE_QUESTION_MARKERS)
        and not question_only
    )


def is_display_diagnosis_request(message: str) -> bool:
    """识别当前项目中显示设备无输出的故障诊断请求。

    “为什么屏幕还是没亮”虽然是疑问句，但它依赖当前工程源码和运行证据，
    不是泛知识问答。只对高置信度的显示故障触发项目检查；用户明确要求改
    代码时仍交给固件工作流处理。
    """

    text = message.strip().casefold()
    return bool(
        text
        and any(marker in text for marker in _DISPLAY_MARKERS)
        and any(marker in text for marker in _DISPLAY_SYMPTOM_MARKERS)
        and not any(marker in text for marker in _DISPLAY_MODIFICATION_MARKERS)
    )


class ConversationResponsePlan(BaseModel):
    """Soft guidance for how the latest message should be answered.

    This is deliberately a plan rather than a hard simple/normal classifier.
    The router can choose a focused direct reply, ask for clarification, or
    spend more time retrieving evidence based on the current context.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["direct_answer", "clarify", "retrieve", "workflow"]
    context_required: bool = False
    scope: Literal["focused", "broad"] = "focused"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    ambiguity: float = Field(default=0.0, ge=0.0, le=1.0)
    answer_budget: int = Field(default=600, ge=120, le=4000)


class ConversationDecision(BaseModel):
    """A validated model decision for the conversation entry mode."""

    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Literal[
        "casual_chat",
        "workflow_status",
        "project_inspection",
        "firmware_task",
        "knowledge_task",
    ]
    response: str = ""
    response_plan: ConversationResponsePlan | None = None

    @model_validator(mode="after")
    def normalize_response_plan(self) -> "ConversationDecision":
        if self.response_plan is None:
            operation = (
                "direct_answer"
                if self.intent in {"casual_chat", "workflow_status"}
                else "retrieve"
                if self.intent == "knowledge_task"
                else "workflow"
            )
            self.response_plan = ConversationResponsePlan(operation=operation)
        self.response = self.response.strip()
        can_answer_now = self.intent in {
            "casual_chat",
            "workflow_status",
        } or (
            self.intent in {"knowledge_task", "project_inspection"}
            and self.response_plan.operation in {"direct_answer", "clarify"}
        )
        if can_answer_now and not self.response:
            raise ValueError("直接回答模式必须包含回复")
        if not can_answer_now:
            self.response = ""
        return self


__all__ = [
    "ConversationDecision",
    "ConversationResponsePlan",
    "explicit_pdf_read_path",
    "is_display_diagnosis_request",
    "is_explicit_firmware_command",
    "is_explicit_pdf_read_request",
]
