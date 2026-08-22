"""Version-scoped, local RAG index for official ESP-IDF example metadata."""

from __future__ import annotations

import hashlib
import threading
import uuid
from dataclasses import dataclass
from typing import Protocol

from luxar.database.persistence import KnowledgeMatch
from luxar.knowledge import EmbeddingPort


@dataclass(frozen=True)
class SdkExampleDocument:
    path: str
    title: str
    content: str
    metadata: dict[str, object]


class SdkKnowledgeIndex(Protocol):
    def document_hashes(self, project_key: str) -> dict[str, str]: ...

    def replace_scope_documents(
        self,
        *,
        project_key: str,
        documents: list[dict[str, object]],
    ) -> None: ...

    def search_knowledge(
        self,
        *,
        project_key: str,
        query_text: str,
        query_embedding: list[float],
        limit: int = 6,
    ) -> list[KnowledgeMatch]: ...

    def search_sdk_knowledge(
        self,
        *,
        project_key: str,
        query_text: str,
        query_embedding: list[float],
        limit: int = 6,
    ) -> list[KnowledgeMatch]: ...

    def count_knowledge_documents(self, project_key: str) -> int: ...


_QUERY_EXPANSIONS = {
    "呼吸灯": "ledc fade pwm led duty cycle",
    "渐变灯": "ledc fade pwm led duty cycle",
    "闪烁": "blink gpio led",
    "配网": "wifi provisioning protocomm",
    "蓝牙": "bluetooth ble nimble gap gatt",
    "低功耗": "deep sleep light sleep power management",
    "串口": "uart console serial",
    "摄像头": "camera jpeg image",
}


class SdkExampleKnowledgeBase:
    """Own SDK metadata synchronization and hybrid retrieval for one index."""

    def __init__(self, index: SdkKnowledgeIndex, embeddings: EmbeddingPort) -> None:
        self._index = index
        self._embeddings = embeddings
        self._synchronized: set[str] = set()
        self._lock = threading.RLock()

    @staticmethod
    def scope(version: str) -> str:
        normalized = version.strip().removeprefix("ESP-IDF ").removeprefix("v")
        return f"sdk:espidf:{normalized or 'unknown'}"

    def sync(
        self,
        *,
        version: str,
        documents: list[SdkExampleDocument],
    ) -> bool:
        scope = self.scope(version)
        with self._lock:
            prepared: list[dict[str, object]] = []
            current_hashes = self._index.document_hashes(scope)
            desired_hashes: dict[str, str] = {}
            for document in documents:
                source_uri = f"espidf-example://{document.path}"
                content_hash = hashlib.sha256(
                    document.content.encode("utf-8")
                ).hexdigest()
                desired_hashes[source_uri] = content_hash
                prepared.append(
                    {
                        "document_id": str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"luxar:{scope}:{source_uri}",
                            )
                        ),
                        "source_uri": source_uri,
                        "title": document.title,
                        "content_hash": content_hash,
                        "metadata": {
                            **document.metadata,
                            "sdk_version": version,
                        },
                        "content": document.content,
                    }
                )
            if current_hashes == desired_hashes:
                self._synchronized.add(scope)
                return False

            vectors = self._embeddings.embed(
                [str(document["content"]) for document in prepared]
            )
            for document, vector in zip(prepared, vectors, strict=True):
                document["vector"] = vector
            self._index.replace_scope_documents(
                project_key=scope,
                documents=prepared,
            )
            self._synchronized.add(scope)
            return True

    def synchronized(self, version: str) -> bool:
        with self._lock:
            return self.scope(version) in self._synchronized

    @staticmethod
    def expand_query(query: str) -> str:
        expansions = [
            value for key, value in _QUERY_EXPANSIONS.items() if key in query
        ]
        return " ".join([query, *expansions]).strip()

    def search(
        self,
        *,
        version: str,
        query: str,
        limit: int = 8,
    ) -> list[KnowledgeMatch]:
        expanded = self.expand_query(query)
        vector = self._embeddings.embed([expanded])[0]
        return self._index.search_sdk_knowledge(
            project_key=self.scope(version),
            query_text=expanded,
            query_embedding=vector,
            limit=limit,
        )

    def document_count(self, version: str) -> int:
        return self._index.count_knowledge_documents(self.scope(version))
