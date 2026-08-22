from __future__ import annotations

from luxar.database.persistence import (
    KnowledgeMatch,
    TransientPersistence,
)
from luxar.knowledge import KnowledgeService, ProjectContextProvider


class FakeEmbeddings:
    dimensions = 1536

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] + [0.0] * 1535 for text in texts]


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
    assert all(len(item[2]) == 1536 for item in chunks)


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
