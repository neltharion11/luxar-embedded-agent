from pathlib import Path
from types import SimpleNamespace

import pytest

from luxar.application.nodes import analyze_knowledge_task, execute_knowledge_task
from luxar.application.results import user_message_for_state
from luxar.document_reader import PdfBatch
from luxar.domain.document_analysis import PdfTechnicalReport
from luxar.domain.knowledge_tasks import KnowledgeTask
from luxar.ports.errors import CapabilityError


class StubPdfReader:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def iter_batches(self, path: Path):
        self.paths.append(path)
        yield PdfBatch(1, 2, 2, "OLED controller SSD1306, I2C address 0x3C", False)


class StubPdfAnalyzer:
    def analyze(self, **_: object) -> PdfTechnicalReport:
        return PdfTechnicalReport(
            answer="该 OLED 使用 I2C，地址为 0x3C。",
            technical_context="器件：OLED；总线：I2C；地址：0x3C。",
        )


class FailingPdfAnalyzer:
    def analyze(self, **_: object) -> PdfTechnicalReport:
        raise CapabilityError(
            category="service",
            message="analysis service unavailable",
            retryable=True,
        )


class PartiallyDegradedPdfAnalyzer:
    def analyze(self, **_: object) -> PdfTechnicalReport:
        return PdfTechnicalReport(
            answer="已从其余页面提取 I2C 地址 0x3C。",
            technical_context="总线：I2C；地址：0x3C。",
            analysis_warnings=["PDF 技术分析未覆盖第 13–24 页（timeout）。"],
        )


def test_explicit_pdf_command_is_parsed_deterministically_without_model() -> None:
    class FailingParser:
        def parse(self, _: str) -> KnowledgeTask:
            raise AssertionError("explicit PDF commands must not reach the model parser")

    message = (
        '"D:\\download\\中景园电子1.3英寸OLED技术资料V3.0\\'
        '1.3寸横屏规格书.pdf" 那么读取这个PDF'
    )
    runtime = SimpleNamespace(
        context=SimpleNamespace(knowledge_task_parser=FailingParser())
    )

    update = analyze_knowledge_task(
        {"task_text": message, "trace": []},  # type: ignore[arg-type]
        runtime,  # type: ignore[arg-type]
    )

    task = update["knowledge_task"]
    assert isinstance(task, KnowledgeTask)
    assert task.action == "read_pdf"
    assert task.file_path.endswith("1.3寸横屏规格书.pdf")


def test_explicit_absolute_pdf_can_be_read_without_knowledge_database(
    tmp_path: Path,
) -> None:
    external = (tmp_path / "display.pdf").resolve()
    reader = StubPdfReader()
    runtime = SimpleNamespace(context=SimpleNamespace(
        project_path=tmp_path / "project",
        project_key="0:test",
        knowledge_service=None,
        document_reader=reader,
    ))
    state = {
        "task_text": f'读取 "{external}"',
        "knowledge_task": KnowledgeTask(
            action="read_pdf",
            summary="读取 OLED 数据手册",
            file_path=str(external),
        ),
        "trace": [],
    }

    update = execute_knowledge_task(state, runtime)  # type: ignore[arg-type]

    result = update["knowledge_result"]
    assert result["read_pdf"] is True  # type: ignore[index]
    assert result["total_pages"] == 2  # type: ignore[index]
    assert reader.paths == [external]
    message = user_message_for_state({"status": "completed", **update})  # type: ignore[arg-type]
    assert "PDF 已完整分批读取" in message
    assert "SSD1306" in message


def test_model_cannot_invent_an_unmentioned_absolute_pdf_path(
    tmp_path: Path,
) -> None:
    runtime = SimpleNamespace(context=SimpleNamespace(
        project_path=tmp_path,
        project_key="0:test",
        knowledge_service=None,
        document_reader=StubPdfReader(),
    ))
    state = {
        "task_text": "读取我刚才说的文档",
        "knowledge_task": KnowledgeTask(
            action="read_pdf",
            summary="读取文档",
            file_path=str((tmp_path / "invented.pdf").resolve()),
        ),
    }

    with pytest.raises(CapabilityError, match="并非用户明确授权"):
        execute_knowledge_task(state, runtime)  # type: ignore[arg-type]


def test_pdf_extraction_is_analyzed_and_returned_as_engineering_context(
    tmp_path: Path,
) -> None:
    external = (tmp_path / "display.pdf").resolve()
    runtime = SimpleNamespace(context=SimpleNamespace(
        project_path=tmp_path / "project",
        project_key="0:test",
        knowledge_service=None,
        document_reader=StubPdfReader(),
        document_analyzer=StubPdfAnalyzer(),
    ))
    state = {
        "task_text": f'读取并分析 "{external}"',
        "knowledge_task": KnowledgeTask(
            action="read_pdf",
            summary="分析 OLED 数据手册",
            file_path=str(external),
        ),
        "trace": [],
    }

    update = execute_knowledge_task(state, runtime)  # type: ignore[arg-type]
    result = update["knowledge_result"]

    assert result["answer"] == "该 OLED 使用 I2C，地址为 0x3C。"  # type: ignore[index]
    assert "总线：I2C" in result["technical_context"]  # type: ignore[index]
    assert result["preview"] == ""  # type: ignore[index]
    message = user_message_for_state({"status": "completed", **update})  # type: ignore[arg-type]
    assert "该 OLED 使用 I2C，地址为 0x3C" in message
    assert "读取内容预览" not in message


def test_pdf_analysis_failure_returns_successful_read_with_degraded_preview(
    tmp_path: Path,
) -> None:
    external = (tmp_path / "display.pdf").resolve()
    runtime = SimpleNamespace(context=SimpleNamespace(
        project_path=tmp_path / "project",
        project_key="0:test",
        knowledge_service=None,
        document_reader=StubPdfReader(),
        document_analyzer=FailingPdfAnalyzer(),
    ))
    state = {
        "task_text": f'读取并分析 "{external}"',
        "knowledge_task": KnowledgeTask(
            action="read_pdf",
            summary="分析 OLED 数据手册",
            file_path=str(external),
        ),
        "trace": [],
    }

    update = execute_knowledge_task(state, runtime)  # type: ignore[arg-type]
    result = update["knowledge_result"]

    assert result["read_pdf"] is True  # type: ignore[index]
    assert result["degraded"] is True  # type: ignore[index]
    assert "智能技术提炼服务暂不可用" in result["analysis_warning"]  # type: ignore[index]
    assert "SSD1306" in result["preview"]  # type: ignore[index]


def test_partial_pdf_analysis_keeps_answer_and_marks_result_degraded(
    tmp_path: Path,
) -> None:
    external = (tmp_path / "display.pdf").resolve()
    runtime = SimpleNamespace(context=SimpleNamespace(
        project_path=tmp_path / "project",
        project_key="0:test",
        knowledge_service=None,
        document_reader=StubPdfReader(),
        document_analyzer=PartiallyDegradedPdfAnalyzer(),
    ))
    state = {
        "task_text": f'读取并分析 "{external}"',
        "knowledge_task": KnowledgeTask(
            action="read_pdf",
            summary="分析 OLED 数据手册",
            file_path=str(external),
        ),
        "trace": [],
    }

    update = execute_knowledge_task(state, runtime)  # type: ignore[arg-type]
    result = update["knowledge_result"]

    assert result["degraded"] is True  # type: ignore[index]
    assert "第 13–24 页" in result["analysis_warning"]  # type: ignore[index]
    assert "I2C 地址 0x3C" in result["answer"]  # type: ignore[index]
    assert result["preview"] == ""  # type: ignore[index]
