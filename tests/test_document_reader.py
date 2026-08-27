from pathlib import Path
import threading
import time

import fitz
import pytest

from luxar.document_reader import DocumentVisionSettings, PdfDocumentReader
from luxar.knowledge import KnowledgeService, LocalHashEmbeddingAdapter
from luxar.lance_knowledge import LanceDBKnowledgeIndex


def test_local_http_vision_settings_do_not_require_api_key() -> None:
    settings = DocumentVisionSettings(
        base_url="http://localhost:1234/v1",
        model="qwen2-vl",
    )

    assert settings.configured is True


def _pdf(path: Path, pages: int) -> None:
    with fitz.open() as document:
        for number in range(1, pages + 1):
            page = document.new_page()
            page.insert_text((72, 72), f"I2C device page {number}: address 0x3C")
        document.save(path)


def test_pdf_without_outline_falls_back_to_fixed_page_batches(tmp_path: Path) -> None:
    path = tmp_path / "device.pdf"
    _pdf(path, 37)
    reader = PdfDocumentReader(characters_per_batch=20_000)

    batches = list(reader.iter_batches(path))

    assert [(item.start_page, item.end_page) for item in batches] == [
        (1, 12), (13, 24), (25, 36), (37, 37),
    ]
    assert batches[0].section_title == "未识别章节（第 1–12 页）"
    assert batches[-1].has_more is False
    assert "page 37" in batches[-1].content


@pytest.mark.parametrize(
    ("pages", "expected"),
    [(1, [(1, 1)]), (12, [(1, 12)]), (13, [(1, 12), (13, 13)])],
)
def test_pdf_page_fallback_boundaries(
    tmp_path: Path,
    pages: int,
    expected: list[tuple[int, int]],
) -> None:
    path = tmp_path / f"device-{pages}.pdf"
    _pdf(path, pages)

    batches = list(PdfDocumentReader().iter_batches(path))

    assert [(item.start_page, item.end_page) for item in batches] == expected


def test_pdf_batches_and_document_crud_are_persisted(tmp_path: Path) -> None:
    path = tmp_path / "device.pdf"
    _pdf(path, 13)
    embeddings = LocalHashEmbeddingAdapter(64)
    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "lance", dimensions=64), embeddings
    )

    imported = service.ingest_pdf(
        project_key="0:test", source_uri="docs/device.pdf", title="Device",
        path=path, reader=PdfDocumentReader(pages_per_batch=5),
    )

    assert imported.total_pages == 13
    assert imported.batches == 3
    assert imported.knowledge_units == 13
    documents = service.list_documents("0:test")
    assert len(documents) == 1
    document_id = str(documents[0]["document_id"])
    assert service.get_document(project_key="0:test", document_id=document_id)
    assert service.delete_document(project_key="0:test", document_id=document_id)
    assert len(service.list_documents("0:test")) == 0


def test_pdf_outline_defines_chapter_units_before_semantic_extraction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "chapters.pdf"
    with fitz.open() as document:
        for number in range(1, 6):
            page = document.new_page()
            page.insert_text((72, 72), f"technical content {number}")
        document.set_toc([
            [1, "接口与电气特性", 1],
            [1, "寄存器与命令", 3],
        ])
        document.save(path)

    batches = list(PdfDocumentReader().iter_batches(path))

    assert [(item.section_title, item.start_page, item.end_page) for item in batches] == [
        ("接口与电气特性", 1, 2),
        ("寄存器与命令", 3, 5),
    ]
    assert batches[0].section_path == ("接口与电气特性",)
    assert "technical content 2" in batches[0].content
    assert "technical content 3" not in batches[0].content


def test_duplicate_outline_entries_on_one_page_use_page_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "malformed-outline.pdf"
    with fitz.open() as document:
        for number in range(1, 14):
            page = document.new_page()
            page.insert_text((72, 72), f"technical content {number}")
        document.set_toc([
            [1, "Conditions", 1],
            [1, "Criteria", 1],
        ])
        document.save(path)

    batches = list(PdfDocumentReader().iter_batches(path))

    assert [(item.start_page, item.end_page) for item in batches] == [
        (1, 12), (13, 13),
    ]


def test_long_recognized_chapter_splits_only_within_its_section(
    tmp_path: Path,
) -> None:
    path = tmp_path / "long-chapter.pdf"
    with fitz.open() as document:
        for number in range(1, 7):
            page = document.new_page()
            for line in range(20):
                page.insert_text(
                    (72, 72 + line * 20),
                    f"chapter page {number} register timing line {line} " * 2,
                    fontsize=8,
                )
        document.set_toc([[1, "寄存器与命令", 1]])
        document.save(path)

    batches = list(PdfDocumentReader(
        pages_per_batch=2,
        characters_per_batch=1_000,
    ).iter_batches(path))

    assert len(batches) > 1
    assert all(batch.section_path == ("寄存器与命令",) for batch in batches)
    assert all("未识别章节" not in batch.section_title for batch in batches)


def test_pdf_without_outline_uses_heading_font_to_infer_sections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "heading-sections.pdf"
    with fitz.open() as document:
        for heading, body in [
            ("Electrical Characteristics", "supply voltage details"),
            (None, "continued electrical details"),
            ("Register Description", "command register details"),
        ]:
            page = document.new_page()
            if heading:
                page.insert_text((72, 72), heading, fontsize=18)
                page.insert_text((72, 110), body, fontsize=10)
            else:
                page.insert_text((72, 72), body, fontsize=10)
        document.save(path)

    batches = list(PdfDocumentReader().iter_batches(path))

    assert [(item.section_title, item.start_page, item.end_page) for item in batches] == [
        ("Electrical Characteristics", 1, 2),
        ("Register Description", 3, 3),
    ]


def test_single_inferred_heading_is_not_trusted_as_full_document_structure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "single-heading.pdf"
    with fitz.open() as document:
        for number in range(1, 15):
            page = document.new_page()
            if number == 1:
                page.insert_text((72, 72), "Criteria", fontsize=18)
                page.insert_text((72, 110), "cover table", fontsize=10)
            else:
                page.insert_text((72, 72), f"technical content {number}", fontsize=10)
        document.save(path)

    batches = list(PdfDocumentReader().iter_batches(path))

    assert [(item.start_page, item.end_page) for item in batches] == [
        (1, 12), (13, 14),
    ]
    assert all("未识别章节" in item.section_title for item in batches)


def test_vision_failure_degrades_to_local_pdf_text(tmp_path: Path) -> None:
    class FailingVisionAnalyzer:
        calls = 0

        def analyze_page(self, **_: object) -> str:
            self.calls += 1
            raise RuntimeError("vision service unavailable")

    path = tmp_path / "drawing.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "OLED I2C address 0x3C")
        page.draw_rect(fitz.Rect(72, 100, 180, 160))
        document.save(path)

    analyzer = FailingVisionAnalyzer()
    batches = list(
        PdfDocumentReader(
            drawing_analyzer=analyzer,
        ).iter_batches(path)
    )

    assert analyzer.calls == 1
    assert "OLED I2C address 0x3C" in batches[0].content
    assert "工程图分析失败：第 1 页" in batches[0].content
    assert batches[0].analysis_warnings


def test_pdf_vision_analysis_has_a_per_document_page_budget(tmp_path: Path) -> None:
    class CountingVisionAnalyzer:
        def __init__(self) -> None:
            self.pages: list[int] = []

        def analyze_page(self, *, page_number: int, **_: object) -> str:
            self.pages.append(page_number)
            return f"diagram page {page_number}"

    path = tmp_path / "many-drawings.pdf"
    with fitz.open() as document:
        for number in range(1, 13):
            page = document.new_page()
            page.insert_text((72, 72), f"Interface timing page {number}")
            page.draw_rect(fitz.Rect(72, 100, 180, 160))
        document.save(path)
    analyzer = CountingVisionAnalyzer()

    batches = list(PdfDocumentReader(
        drawing_analyzer=analyzer,
        max_visual_pages=3,
    ).iter_batches(path))

    assert len(analyzer.pages) == 3
    assert sum(batch.content.count("[工程图分析]") for batch in batches) == 3


def test_online_vision_analysis_runs_concurrently_and_restores_page_order(
    tmp_path: Path,
) -> None:
    class ConcurrentVisionAnalyzer:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def analyze_page(self, *, page_number: int, **_: object) -> str:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.04 * (5 - page_number))
            with self.lock:
                self.active -= 1
            return f"diagram page {page_number}"

    path = tmp_path / "parallel-drawings.pdf"
    with fitz.open() as document:
        for number in range(1, 5):
            page = document.new_page()
            page.insert_text((72, 72), f"Interface timing page {number}")
            page.draw_rect(fitz.Rect(72, 100, 180, 160))
        document.save(path)
    analyzer = ConcurrentVisionAnalyzer()

    batches = list(PdfDocumentReader(
        drawing_analyzer=analyzer,
        max_visual_pages=4,
        visual_max_workers=4,
    ).iter_batches(path))

    assert analyzer.max_active > 1
    assert analyzer.max_active <= 4
    content = "\n".join(batch.content for batch in batches)
    assert content.index("diagram page 1") < content.index("diagram page 4")


def test_local_vision_analysis_is_strictly_sequential(tmp_path: Path) -> None:
    class SequentialVisionAnalyzer:
        def __init__(self) -> None:
            self.pages: list[int] = []
            self.active = 0
            self.max_active = 0

        def analyze_page(self, *, page_number: int, **_: object) -> str:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.pages.append(page_number)
            self.active -= 1
            return f"diagram page {page_number}"

    path = tmp_path / "local-drawings.pdf"
    with fitz.open() as document:
        for number in range(1, 4):
            page = document.new_page()
            page.insert_text((72, 72), f"Interface page {number}")
            page.draw_rect(fitz.Rect(72, 100, 180, 160))
        document.save(path)
    analyzer = SequentialVisionAnalyzer()

    list(PdfDocumentReader(
        drawing_analyzer=analyzer,
        visual_max_workers=1,
    ).iter_batches(path))

    assert analyzer.pages == [1, 2, 3]
    assert analyzer.max_active == 1


def test_online_vision_partial_failure_keeps_successful_pages_and_warning(
    tmp_path: Path,
) -> None:
    class PartialVisionAnalyzer:
        def analyze_page(self, *, page_number: int, **_: object) -> str:
            if page_number == 2:
                raise RuntimeError("page failed")
            return f"diagram page {page_number}"

    path = tmp_path / "partial-vision.pdf"
    with fitz.open() as document:
        for number in range(1, 4):
            page = document.new_page()
            page.insert_text((72, 72), f"Interface page {number}")
            page.draw_rect(fitz.Rect(72, 100, 180, 160))
        document.save(path)

    batches = list(PdfDocumentReader(
        drawing_analyzer=PartialVisionAnalyzer(),
        visual_max_workers=4,
    ).iter_batches(path))

    content = "\n".join(batch.content for batch in batches)
    warnings = [item for batch in batches for item in batch.analysis_warnings]
    assert "diagram page 1" in content
    assert "diagram page 3" in content
    assert any("第 2 页" in item for item in warnings)


def test_blank_and_image_only_pages_survive_page_fallback(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    with fitz.open() as document:
        document.new_page()
        image_page = document.new_page()
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
        pixmap.clear_with(255)
        image_page.insert_image(
            fitz.Rect(72, 72, 172, 172),
            pixmap=pixmap,
        )
        document.save(path)

    batches = list(PdfDocumentReader(
        pages_per_batch=1,
        drawing_analyzer=None,
    ).iter_batches(path))

    assert [(item.start_page, item.end_page) for item in batches] == [(1, 1), (2, 2)]
    assert all("[无可提取文本]" in batch.content for batch in batches)
    assert "嵌入图像 1" in batches[1].content
