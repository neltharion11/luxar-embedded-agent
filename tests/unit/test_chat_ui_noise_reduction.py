from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = REPO_ROOT / "ui" / "public" / "index.html"


def _index_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_chat_ui_has_single_tool_output_renderer() -> None:
    html = _index_html()

    assert html.count("function renderToolOutput(") == 1
    assert html.count("function renderToolOutputRefine(") == 1


def test_successful_tool_progress_is_not_appended_to_chat_markdown() -> None:
    html = _index_html()

    assert "formatToolRunningLine" not in html
    assert "var toolLine" not in html
    assert "streamContent += '\\n\\n' + toolLine" not in html
    assert "logData.message && logData.success === false" in html


def test_edited_file_outputs_are_grouped_and_read_outputs_are_hidden() -> None:
    html = _index_html()

    assert "function createEditedFilesController" in html
    assert "'\\u5df2\\u7f16\\u8f91 ' + count + ' \\u4e2a\\u6587\\u4ef6'" in html
    assert "edited-file-content" in html
    assert "'\\u4fee\\u6539\\u5185\\u5bb9'" in html
    assert "eventData.tool === 'workspace_write_file'" in html
    assert "editedFilesController.add(outputData)" in html
    assert "function isFailureToolOutput" not in html
    assert "shouldRenderVerboseToolOutput(outputData)" in html

    tool_output_branch = re.search(
        r"currentEvent === 'tool_output'(?P<body>.*?)currentEvent === 'serial_line'",
        html,
        re.DOTALL,
    )
    assert tool_output_branch is not None
    body = tool_output_branch.group("body")
    assert "isEditableToolOutput(outputData)" in body
    assert "shouldRenderVerboseToolOutput(outputData)" in body
    assert "renderToolOutput(bubbleDiv, outputData, container)" in body
    assert "workspace_read_file" not in body
    assert "workspace_shell" not in body


def test_read_outputs_are_not_rendered_even_when_content_mentions_errors() -> None:
    html = _index_html()

    render_policy = re.search(
        r"function shouldRenderVerboseToolOutput\(eventData\) \{(?P<body>.*?)\n\}",
        html,
        re.DOTALL,
    )
    assert render_policy is not None
    policy_body = render_policy.group("body")

    assert "failed" not in policy_body.lower()
    assert "error" not in policy_body.lower()
    assert "失败" not in policy_body
    assert "return eventData && eventData.tool === 'workspace_build' && !!eventData.content;" in html
