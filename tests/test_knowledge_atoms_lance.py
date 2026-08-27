from __future__ import annotations

from pathlib import Path

from luxar.document_reader import PdfBatch
from luxar.knowledge import KnowledgeService, LocalHashEmbeddingAdapter
from luxar.knowledge_extraction import SemanticKnowledgeAtomExtractor
from luxar.lance_knowledge import LanceDBKnowledgeIndex


class FixedReader:
    def iter_batches(self, path: Path):
        del path
        yield PdfBatch(
            1,
            2,
            2,
            "## 第 1 页\n### GPIO34\nGPIO34 只能作为输入使用。\n\n"
            "## 第 2 页\n### GPIO0\nGPIO0 是启动绑带引脚。",
            False,
        )


def test_lance_indexes_atoms_as_one_document_with_provenance(tmp_path: Path) -> None:
    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )

    imported = service.ingest_pdf(
        project_key="0:esp32",
        source_uri="docs/esp32.pdf",
        title="ESP32 数据手册",
        path=tmp_path / "not-opened.pdf",
        reader=FixedReader(),
        extractor=SemanticKnowledgeAtomExtractor(),
    )
    matches = service.search(
        project_key="0:esp32",
        query="GPIO34 输入限制",
        limit=4,
    )

    assert imported.knowledge_units == 2
    assert len(service.list_documents("0:esp32")) == 1
    assert matches
    assert matches[0].knowledge_id is not None
    assert matches[0].subject in {"GPIO34", "GPIO0"}
    assert matches[0].title == "ESP32 数据手册"
    assert "第 1-2 页" not in matches[0].title
    assert matches[0].source_pages
