import json
from pathlib import Path
import threading
import time

import fitz
import pytest

from luxar.adapters.deepseek.document_analyzer import DeepSeekPdfTechnicalAnalyzer
from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.document_reader import PdfBatch, PdfDocumentReader
from luxar.ports.errors import CapabilityError


def test_pdf_analyzer_uses_bounded_multi_round_extraction_and_final_synthesis() -> None:
    client = FakeJsonCompletionClient([
        {
            "relevant": True,
            "facts": [{
                "category": "通信协议",
                "fact": "I2C 地址为 0x3C",
                "evidence_pages": [3],
            }],
            "uncertainties": [],
        },
        {
            "relevant": True,
            "facts": [{
                "category": "引脚",
                "fact": "SCL 和 SDA 为 I2C 信号线",
                "evidence_pages": [4],
            }],
            "uncertainties": [],
        },
        {
            "answer": "该器件通过 I2C 通信，默认地址为 0x3C。",
            "technical_context": "协议：I2C；地址：0x3C；信号：SCL、SDA。",
        },
    ])
    analyzer = DeepSeekPdfTechnicalAnalyzer(
        client, "chat-model", local_model=True
    )
    batch = PdfBatch(
        start_page=1,
        end_page=5,
        total_pages=5,
        content=("datasheet technical content " * 1_600),
        has_more=False,
    )

    report = analyzer.analyze(
        task_text="分析这个器件如何接入 ESP32",
        title="OLED datasheet",
        batches=[batch],
    )

    assert len(client.calls) == 3
    assert "硬件名称" in client.calls[0][0]
    assert "引脚编号" in client.calls[0][0]
    assert "通信协议" in client.calls[0][0]
    assert "初始化" in client.calls[0][0]
    assert "由你判断哪些内容" in client.calls[0][0]
    final_input = json.loads(client.calls[-1][1])
    assert len(final_input["section_analyses"]) == 2
    assert "I2C 地址为 0x3C" in str(final_input["section_analyses"])
    assert "datasheet technical content" not in client.calls[-1][1]
    assert report.technical_context.startswith("协议：I2C")


def test_pymupdf_text_fallback_is_sent_to_conversation_model(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sensor.pdf"
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text(
            (72, 72),
            "SENSOR-X uses I2C address 0x44 and requires 3.3V.",
        )
        document.save(path)

    # drawing_analyzer=None is the explicit PyMuPDF-only fallback.
    batches = list(PdfDocumentReader(drawing_analyzer=None).iter_batches(path))
    client = FakeJsonCompletionClient([
        {
            "relevant": True,
            "facts": [{
                "category": "通信与供电",
                "fact": "SENSOR-X 使用 I2C 地址 0x44，供电 3.3V",
                "evidence_pages": [1],
            }],
            "uncertainties": [],
        },
        {
            "answer": "SENSOR-X 使用 I2C，地址 0x44，供电 3.3V。",
            "technical_context": "型号：SENSOR-X；I2C：0x44；供电：3.3V。",
        },
    ])

    report = DeepSeekPdfTechnicalAnalyzer(
        client, "conversation-model", local_model=True
    ).analyze(
        task_text="分析这个传感器",
        title="SENSOR-X datasheet",
        batches=batches,
    )

    first_model_input = json.loads(client.calls[0][1])
    assert "SENSOR-X uses I2C address 0x44" in first_model_input["extracted_content"]
    assert client.calls[0][2] == "conversation-model"
    assert report.technical_context.endswith("供电：3.3V。")


class _ConcurrentPdfClient:
    def __init__(self, failed_sections: set[int] | None = None) -> None:
        self.failed_sections = failed_sections or set()
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.started: list[int] = []
        self.final_input: dict[str, object] | None = None

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> dict[str, object]:
        del model
        payload = json.loads(user_prompt)
        if "最终答复" in system_prompt:
            self.final_input = payload
            return {
                "answer": "按文档顺序汇总完成。",
                "technical_context": "ordered technical context",
            }
        section = int(payload["section_number"])
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started.append(section)
        try:
            time.sleep(0.03 * (5 - section))
            if section in self.failed_sections:
                raise CapabilityError(
                    category="timeout",
                    message="chunk timeout",
                    retryable=True,
                )
            return {
                "relevant": True,
                "facts": [{
                    "category": "section",
                    "fact": f"fact-{section}",
                    "evidence_pages": [section],
                }],
                "uncertainties": [],
            }
        finally:
            with self.lock:
                self.active -= 1


def _four_pdf_batches() -> list[PdfBatch]:
    return [
        PdfBatch(
            number,
            number,
            4,
            f"section content {number}",
            number < 4,
            section_title=f"section-{number}",
        )
        for number in range(1, 5)
    ]


def test_online_pdf_analysis_is_concurrent_but_final_input_stays_ordered() -> None:
    client = _ConcurrentPdfClient()
    progress = []

    report = DeepSeekPdfTechnicalAnalyzer(
        client, "online-model", local_model=False, max_workers=4
    ).analyze(
        task_text="提取驱动知识",
        title="datasheet",
        batches=_four_pdf_batches(),
        progress_reporter=progress.append,
    )

    assert client.max_active > 1
    assert client.max_active <= 4
    assert client.final_input is not None
    analyses = client.final_input["section_analyses"]
    assert [item["facts"][0]["fact"] for item in analyses] == [
        "fact-1", "fact-2", "fact-3", "fact-4",
    ]
    assert "在线并发分析完成 4/4 块" in progress[-1].message
    assert report.analysis_warnings == []


def test_local_pdf_analysis_is_strictly_sequential() -> None:
    client = _ConcurrentPdfClient()

    DeepSeekPdfTechnicalAnalyzer(
        client, "local-model", local_model=True, max_workers=4
    ).analyze(
        task_text="提取驱动知识",
        title="datasheet",
        batches=_four_pdf_batches(),
    )

    assert client.max_active == 1
    assert client.started == [1, 2, 3, 4]


def test_online_pdf_analysis_keeps_successful_chunks_and_warns_on_failure() -> None:
    client = _ConcurrentPdfClient({2})

    report = DeepSeekPdfTechnicalAnalyzer(
        client, "online-model", local_model=False, max_workers=4
    ).analyze(
        task_text="提取驱动知识",
        title="datasheet",
        batches=_four_pdf_batches(),
    )

    assert client.final_input is not None
    analyses = client.final_input["section_analyses"]
    assert [item["facts"][0]["fact"] for item in analyses] == [
        "fact-1", "fact-3", "fact-4",
    ]
    assert any("第 2–2 页" in item for item in report.analysis_warnings)
    assert any("timeout" in item for item in report.analysis_warnings)


def test_pdf_analysis_raises_only_when_all_chunks_fail() -> None:
    client = _ConcurrentPdfClient({1, 2, 3, 4})

    with pytest.raises(CapabilityError, match="所有分块"):
        DeepSeekPdfTechnicalAnalyzer(
            client, "online-model", local_model=False, max_workers=4
        ).analyze(
            task_text="提取驱动知识",
            title="datasheet",
            batches=_four_pdf_batches(),
        )

    assert client.final_input is None
