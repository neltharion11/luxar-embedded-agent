from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from luxar.document_reader import PdfDocumentReader, PdfReadProgress


class _FakePage:
    def __init__(self, number: int) -> None:
        self.number = number

    def get_text(self, _: str) -> str:
        return f"page {self.number}"

    def get_images(self, *, full: bool) -> list[object]:
        assert full is True
        return []

    def get_drawings(self) -> list[object]:
        return []


class _FakeDocument:
    page_count = 3

    def __enter__(self) -> _FakeDocument:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def load_page(self, index: int) -> _FakePage:
        return _FakePage(index + 1)

    def get_toc(self, *, simple: bool) -> list[object]:
        assert simple is True
        return []


def test_pdf_reader_reports_opening_each_page_and_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF-fake")
    monkeypatch.setitem(
        sys.modules,
        "fitz",
        SimpleNamespace(open=lambda _: _FakeDocument()),
    )
    progress: list[PdfReadProgress] = []

    batches = list(PdfDocumentReader(pages_per_batch=2).iter_batches(
        pdf,
        progress_reporter=progress.append,
    ))

    assert [(item.start_page, item.end_page) for item in batches] == [(1, 2), (3, 3)]
    assert batches[0].section_title == "未识别章节（第 1–2 页）"
    assert [item.phase for item in progress] == [
        "opening",
        "extracting",
        "extracting",
        "extracting",
        "extracted",
    ]
    assert [item.completed_pages for item in progress] == [0, 1, 2, 3, 3]
    assert all(item.total_pages == 3 for item in progress)
    assert progress[-1].message == "章节识别失败，已按每 2 页划分为 2 块"
