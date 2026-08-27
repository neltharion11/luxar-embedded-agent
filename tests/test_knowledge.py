from __future__ import annotations

from types import SimpleNamespace

import pytest

from luxar.database.persistence import (
    KnowledgeMatch,
    TransientPersistence,
)
from luxar.knowledge import (
    KnowledgeService,
    KnowledgeSettings,
    OpenAIEmbeddingAdapter,
    ProjectContextProvider,
)
from luxar.document_reader import PdfBatch
from luxar.knowledge_extraction import SemanticKnowledgeAtomExtractor


class FakeEmbeddings:
    dimensions = 1536

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] + [0.0] * 1535 for text in texts]


def test_openai_embedding_adapter_rejects_provider_dimension_mismatch() -> None:
    class StubEmbeddings:
        def create(self, **_: object) -> object:
            return SimpleNamespace(
                data=[SimpleNamespace(index=0, embedding=[0.0] * 5)]
            )

    adapter = OpenAIEmbeddingAdapter(
        KnowledgeSettings(api_key="test", model="embedding-test", dimensions=3),
        client=SimpleNamespace(embeddings=StubEmbeddings()),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="期望 3，实际 5"):
        adapter.embed(["dimension probe"])


class KnowledgePersistence(TransientPersistence):
    durable = True

    def __init__(self) -> None:
        super().__init__()
        self.document: dict[str, object] | None = None

    def replace_document(self, **values: object) -> None:
        self.document = dict(values)

    def search_knowledge(self, **_: object) -> list[KnowledgeMatch]:
        return [
            KnowledgeMatch(
                document_id="doc",
                title="ESP-IDF",
                source_uri="manual://idf",
                ordinal=0,
                content="Use idf.py build.",
                score=0.95,
            )
        ]

    def list_knowledge_documents(self, project_key: str) -> list[dict[str, object]]:
        del project_key
        return []

    def delete_knowledge_document(self, **_: object) -> bool:
        return False


def test_ingest_chunks_embeds_and_persists_document() -> None:
    persistence = KnowledgePersistence()
    service = KnowledgeService(
        persistence,
        FakeEmbeddings(),
        chunk_characters=200,
        overlap_characters=20,
    )
    result = service.ingest(
        project_key="0:blink",
        source_uri="manual://idf",
        title="ESP-IDF manual",
        content=("build and flash\n" * 40),
    )
    assert result.chunks > 1
    assert persistence.document is not None
    chunks = persistence.document["chunks"]
    assert isinstance(chunks, list)
    assert all(len(item.embedding) == 1536 for item in chunks)
    assert all(item.metadata["unit_type"] == "document_chunk" for item in chunks)


def test_pdf_import_indexes_specific_knowledge_not_page_batches() -> None:
    class Reader:
        def iter_batches(self, path):
            del path
            yield PdfBatch(
                1,
                2,
                2,
                "## 第 1 页\n### GPIO34\nGPIO34 只能作为数字输入使用。\n\n"
                "## 第 2 页\n### ADC2\nWi-Fi 工作时 ADC2 可能被占用。",
                False,
            )

    persistence = KnowledgePersistence()
    service = KnowledgeService(persistence, FakeEmbeddings())
    progress = []
    imported = service.ingest_pdf(
        project_key="0:blink",
        source_uri="docs/esp32.pdf",
        title="ESP32 数据手册",
        path=__import__("pathlib").Path("unused.pdf"),
        reader=Reader(),
        extractor=SemanticKnowledgeAtomExtractor(),
        progress_reporter=progress.append,
    )

    assert imported.batches == 1
    assert imported.knowledge_units == 2
    assert len(imported.documents) == 1
    assert persistence.document is not None
    assert persistence.document["title"] == "ESP32 数据手册"
    chunks = persistence.document["chunks"]
    assert [item.content for item in chunks] == [
        "GPIO34 只能作为数字输入使用。",
        "Wi-Fi 工作时 ADC2 可能被占用。",
    ]
    assert chunks[0].metadata["source_pages"] == [1]
    assert chunks[0].metadata["subject"] == "GPIO34"
    assert [item.phase for item in progress] == [
        "analyzing",
        "indexing",
        "completed",
    ]


def test_project_context_combines_structured_memory_and_cited_knowledge() -> None:
    persistence = KnowledgePersistence()
    persistence.upsert_memory(
        project_key="0:blink",
        memory_key="device.target",
        memory_type="device_config",
        value={"target_chip": "esp32"},
    )
    service = KnowledgeService(persistence, FakeEmbeddings())
    context = ProjectContextProvider(
        persistence, "0:blink", service
    )("how to build")
    assert context["memories"][0]["value"] == {"target_chip": "esp32"}
    assert context["knowledge"][0]["source_uri"] == "manual://idf"


def test_project_context_includes_bounded_previous_completed_run() -> None:
    persistence = KnowledgePersistence()
    persistence.start_run(
        thread_id="previous",
        task_key="0:blink",
        project_name="blink",
        root_index=0,
        task_text="P13 输出低电平",
        runtime_config={},
    )
    persistence.finish_run(
        "previous",
        status="completed",
        result={
            "requirement": {
                "target": "esp32",
                "goal": "gpio_output_low",
                "peripherals": [
                    {"kind": "gpio", "parameters": {"pin": 13}}
                ],
            },
            "project_analysis": {"summary": "P13 output low implemented"},
            "changed_files": ["main/main.c"],
            "build_evidence": {
                "success": True,
                "return_code": 0,
                "stdout_summary": "must not enter prompt",
            },
            "flash_evidence": None,
        },
    )
    persistence.append_exchange(
        "0:blink",
        thread_id="previous",
        user_message="P13 输出低电平",
        assistant_message="构建通过。",
    )

    context = ProjectContextProvider(
        persistence,
        "0:blink",
    )("改成高电平")

    previous = context["previous_completed_run"]
    assert previous["requirement"]["peripherals"][0]["parameters"] == {
        "pin": 13
    }
    assert previous["build_evidence"] == {
        "success": True,
        "return_code": 0,
        "error_category": None,
    }
    assert "stdout_summary" not in str(previous)
    assert context["recent_conversation"][-1]["content"] == "构建通过。"


def test_project_context_carries_analyzed_pdf_facts_into_next_firmware_turn() -> None:
    persistence = KnowledgePersistence()
    persistence.start_run(
        thread_id="pdf-run",
        task_key="0:blink",
        project_name="blink",
        root_index=0,
        task_text="读取 OLED 数据手册",
        runtime_config={},
    )
    persistence.finish_run(
        "pdf-run",
        status="completed",
        result={
            "knowledge_result": {
                "read_pdf": True,
                "title": "OLED 数据手册",
                "total_pages": 37,
                "technical_context": (
                    "型号：SH1106；协议：I2C；地址：0x3C；"
                    "信号线：SCL、SDA。"
                ),
            }
        },
    )

    context = ProjectContextProvider(persistence, "0:blink")(
        "为这个 OLED 编写驱动"
    )

    document = context["previous_completed_run"]["document_context"]
    assert document["title"] == "OLED 数据手册"
    assert "地址：0x3C" in document["technical_context"]
