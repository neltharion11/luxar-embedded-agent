from pathlib import Path

from luxar.database.persistence import KnowledgeMatch
from luxar.lance_knowledge import LanceDBKnowledgeIndex
from luxar.knowledge import LocalHashEmbeddingAdapter
from luxar.sdk_knowledge import SdkExampleDocument, SdkExampleKnowledgeBase


class FakeSdkIndex:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.replacements = 0
        self.documents: dict[str, list[dict[str, object]]] = {}

    def document_hashes(self, project_key: str) -> dict[str, str]:
        return dict(self.hashes.get(project_key, {}))

    def replace_scope_documents(
        self,
        *,
        project_key: str,
        documents: list[dict[str, object]],
    ) -> None:
        self.replacements += 1
        self.documents[project_key] = [dict(item) for item in documents]
        self.hashes[project_key] = {
            str(item["source_uri"]): str(item["content_hash"])
            for item in documents
        }

    def search_knowledge(self, **values: object) -> list[KnowledgeMatch]:
        project_key = str(values["project_key"])
        query = str(values["query_text"])
        assert "ledc fade pwm" in query
        item = self.documents[project_key][0]
        return [
            KnowledgeMatch(
                document_id=str(item["document_id"]),
                title=str(item["title"]),
                source_uri=str(item["source_uri"]),
                ordinal=0,
                content=str(item["content"]),
                score=0.9,
            )
        ]

    def search_sdk_knowledge(self, **values: object) -> list[KnowledgeMatch]:
        return self.search_knowledge(**values)

    def count_knowledge_documents(self, project_key: str) -> int:
        return len(self.documents.get(project_key, []))


def test_sdk_knowledge_uses_version_scope_and_skips_unchanged_sync() -> None:
    index = FakeSdkIndex()
    embeddings = LocalHashEmbeddingAdapter(dimensions=64)
    knowledge = SdkExampleKnowledgeBase(index, embeddings)
    documents = [
        SdkExampleDocument(
            path="peripherals/ledc/ledc_fade",
            title="LEDC fade example",
            content="LEDC fade PWM duty cycle example",
            metadata={"kind": "official_example"},
        )
    ]

    first = knowledge.sync(version="ESP-IDF v6.0.2", documents=documents)
    second = knowledge.sync(version="6.0.2", documents=documents)
    matches = knowledge.search(
        version="6.0.2",
        query="实现呼吸灯",
    )

    assert first is True
    assert second is False
    assert index.replacements == 1
    assert set(index.documents) == {"sdk:espidf:6.0.2"}
    assert matches[0].source_uri == (
        "espidf-example://peripherals/ledc/ledc_fade"
    )


def test_local_hash_embeddings_are_deterministic_and_normalized() -> None:
    embeddings = LocalHashEmbeddingAdapter(dimensions=64)

    first, second = embeddings.embed(["ledc fade pwm", "ledc fade pwm"])

    assert first == second
    assert len(first) == 64
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6


def test_sdk_knowledge_round_trips_through_separate_lancedb(
    tmp_path: Path,
) -> None:
    embeddings = LocalHashEmbeddingAdapter(dimensions=64)
    path = tmp_path / "sdk-knowledge.lance"
    knowledge = SdkExampleKnowledgeBase(
        LanceDBKnowledgeIndex(path, dimensions=64),
        embeddings,
    )
    documents = [
        SdkExampleDocument(
            path="peripherals/ledc/ledc_fade",
            title="LEDC fade",
            content="ledc fade pwm led duty cycle",
            metadata={"kind": "official_example"},
        ),
        SdkExampleDocument(
            path="protocols/http_server/simple",
            title="HTTP server",
            content="http server wifi response",
            metadata={"kind": "official_example"},
        ),
    ]

    assert knowledge.sync(version="6.0.2", documents=documents) is True
    reopened = SdkExampleKnowledgeBase(
        LanceDBKnowledgeIndex(path, dimensions=64),
        embeddings,
    )
    assert reopened.sync(version="6.0.2", documents=documents) is False
    matches = reopened.search(
        version="6.0.2",
        query="实现呼吸灯",
        limit=1,
    )

    assert reopened.document_count("6.0.2") == 2
    assert matches[0].source_uri.endswith("peripherals/ledc/ledc_fade")
