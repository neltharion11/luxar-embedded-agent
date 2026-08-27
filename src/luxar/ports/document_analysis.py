"""Port for turning extracted PDF batches into useful engineering context."""

from __future__ import annotations

from typing import Protocol, Sequence

from luxar.document_reader import PdfBatch, PdfProgressReporter
from luxar.domain.document_analysis import PdfTechnicalReport


class PdfTechnicalAnalyzer(Protocol):
    def analyze(
        self,
        *,
        task_text: str,
        title: str,
        batches: Sequence[PdfBatch],
        progress_reporter: PdfProgressReporter | None = None,
    ) -> PdfTechnicalReport: ...
