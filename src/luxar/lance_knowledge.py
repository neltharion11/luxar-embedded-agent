"""Embedded LanceDB knowledge index used by the local storage profile."""

from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path
from typing import Any

from luxar.database.persistence import KnowledgeMatch


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_SDK_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class LanceDBKnowledgeIndex:
    """Persist documents and vectors locally without a database server."""

    _DOCUMENTS = "luxar_knowledge_documents"
    _CHUNKS = "luxar_knowledge_chunks"

    def __init__(self, path: Path, *, dimensions: int) -> None:
        if dimensions < 1:
            raise ValueError("embedding 维度必须大于 0")
        self.path = path.expanduser().resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        self.dimensions = dimensions
        self._lock = threading.RLock()
        try:
            import lancedb
            import pyarrow as pa
        except ImportError as error:
            raise RuntimeError("未安装 LanceDB") from error
        self._db = lancedb.connect(str(self.path))
        names = set(self._db.list_tables().tables)
        if self._DOCUMENTS not in names:
            self._db.create_table(
                self._DOCUMENTS,
                schema=pa.schema(
                    [
                        pa.field("document_id", pa.string()),
                        pa.field("project_key", pa.string()),
                        pa.field("source_uri", pa.string()),
                        pa.field("title", pa.string()),
                        pa.field("content_hash", pa.string()),
                        pa.field("metadata_json", pa.string()),
                    ]
                ),
            )
        if self._CHUNKS not in names:
            self._db.create_table(
                self._CHUNKS,
                schema=pa.schema(
                    [
                        pa.field("chunk_id", pa.string()),
                        pa.field("document_id", pa.string()),
                        pa.field("project_key", pa.string()),
                        pa.field("source_uri", pa.string()),
                        pa.field("title", pa.string()),
                        pa.field("ordinal", pa.int32()),
                        pa.field("content", pa.string()),
                        pa.field("token_count", pa.int32()),
                        pa.field(
                            "vector",
                            pa.list_(pa.float32(), self.dimensions),
                        ),
                    ]
                ),
            )

    def health(self) -> bool:
        try:
            return self._CHUNKS in set(self._db.list_tables().tables)
        except Exception:
            return False

    def replace_document(
        self,
        *,
        document_id: str,
        project_key: str,
        source_uri: str,
        title: str,
        content_hash: str,
        metadata: dict[str, object],
        chunks: list[tuple[str, int, list[float]]],
    ) -> None:
        for _, _, vector in chunks:
            if len(vector) != self.dimensions:
                raise ValueError("知识向量维度与 LanceDB 配置不一致")
            if any(not math.isfinite(value) for value in vector):
                raise ValueError("知识向量包含非有限值")

        document_filter = f"document_id = {_sql_literal(document_id)}"
        with self._lock:
            documents = self._db.open_table(self._DOCUMENTS)
            chunk_table = self._db.open_table(self._CHUNKS)
            documents.delete(document_filter)
            chunk_table.delete(document_filter)
            documents.add(
                [
                    {
                        "document_id": document_id,
                        "project_key": project_key,
                        "source_uri": source_uri,
                        "title": title,
                        "content_hash": content_hash,
                        "metadata_json": json.dumps(
                            metadata,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    }
                ]
            )
            if chunks:
                chunk_table.add(
                    [
                        {
                            "chunk_id": f"{document_id}:{ordinal}",
                            "document_id": document_id,
                            "project_key": project_key,
                            "source_uri": source_uri,
                            "title": title,
                            "ordinal": ordinal,
                            "content": content,
                            "token_count": token_count,
                            "vector": vector,
                        }
                        for ordinal, (content, token_count, vector) in enumerate(chunks)
                    ]
                )

    def document_hashes(self, project_key: str) -> dict[str, str]:
        """Return the persisted SDK manifest without loading chunk vectors."""

        with self._lock:
            table = self._db.open_table(self._DOCUMENTS)
            rows = table.to_arrow().select(
                ["project_key", "source_uri", "content_hash"]
            ).to_pylist()
        return {
            str(row["source_uri"]): str(row["content_hash"])
            for row in rows
            if str(row["project_key"]) == project_key
        }

    def replace_scope_documents(
        self,
        *,
        project_key: str,
        documents: list[dict[str, object]],
    ) -> None:
        """Atomically replace one logical SDK-version scope in two bulk writes."""

        document_rows: list[dict[str, object]] = []
        chunk_rows: list[dict[str, object]] = []
        for document in documents:
            vector = list(document["vector"])  # type: ignore[arg-type]
            if len(vector) != self.dimensions:
                raise ValueError("知识向量维度与 LanceDB 配置不一致")
            if any(not math.isfinite(float(value)) for value in vector):
                raise ValueError("知识向量包含非有限值")
            document_id = str(document["document_id"])
            source_uri = str(document["source_uri"])
            title = str(document["title"])
            content = str(document["content"])
            document_rows.append(
                {
                    "document_id": document_id,
                    "project_key": project_key,
                    "source_uri": source_uri,
                    "title": title,
                    "content_hash": str(document["content_hash"]),
                    "metadata_json": json.dumps(
                        document.get("metadata", {}),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
            chunk_rows.append(
                {
                    "chunk_id": f"{document_id}:0",
                    "document_id": document_id,
                    "project_key": project_key,
                    "source_uri": source_uri,
                    "title": title,
                    "ordinal": 0,
                    "content": content,
                    "token_count": max(1, len(content) // 4),
                    "vector": [float(value) for value in vector],
                }
            )

        scope_filter = f"project_key = {_sql_literal(project_key)}"
        with self._lock:
            document_table = self._db.open_table(self._DOCUMENTS)
            chunk_table = self._db.open_table(self._CHUNKS)
            document_table.delete(scope_filter)
            chunk_table.delete(scope_filter)
            if document_rows:
                document_table.add(document_rows)
                chunk_table.add(chunk_rows)

    @staticmethod
    def _lexical_score(query: str, content: str) -> float:
        query_tokens = {token.casefold() for token in _TOKEN_RE.findall(query)}
        if not query_tokens:
            return 0.0
        content_tokens = {token.casefold() for token in _TOKEN_RE.findall(content)}
        return len(query_tokens & content_tokens) / len(query_tokens)

    def search_knowledge(
        self,
        *,
        project_key: str,
        query_text: str,
        query_embedding: list[float],
        limit: int = 6,
    ) -> list[KnowledgeMatch]:
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        if len(query_embedding) != self.dimensions:
            raise ValueError("查询向量维度与 LanceDB 配置不一致")
        with self._lock:
            table = self._db.open_table(self._CHUNKS)
            try:
                rows: list[dict[str, Any]] = (
                    table.search(query_embedding)
                    .where(f"project_key = {_sql_literal(project_key)}")
                    .limit(max(limit * 4, 20))
                    .to_list()
                )
            except Exception:
                if table.count_rows() == 0:
                    return []
                raise

        ranked: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            distance = max(float(row.get("_distance", 1.0)), 0.0)
            semantic = 1.0 / (1.0 + distance)
            lexical = self._lexical_score(query_text, str(row["content"]))
            ranked.append((0.8 * semantic + 0.2 * lexical, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            KnowledgeMatch(
                document_id=str(row["document_id"]),
                title=str(row["title"]),
                source_uri=str(row["source_uri"]),
                ordinal=int(row["ordinal"]),
                content=str(row["content"]),
                score=score,
            )
            for score, row in ranked[:limit]
        ]

    def search_sdk_knowledge(
        self,
        *,
        project_key: str,
        query_text: str,
        query_embedding: list[float],
        limit: int = 6,
    ) -> list[KnowledgeMatch]:
        """Hybrid scan for the small, version-scoped official-example corpus."""

        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        if len(query_embedding) != self.dimensions:
            raise ValueError("查询向量维度与 LanceDB 配置不一致")
        query_tokens = set(
            _SDK_TOKEN_RE.findall(
                query_text.casefold().replace("_", " ").replace("-", " ")
            )
        )
        with self._lock:
            table = self._db.open_table(self._CHUNKS)
            rows = table.to_arrow().to_pylist()

        ranked: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            if str(row["project_key"]) != project_key:
                continue
            content_tokens = set(
                _SDK_TOKEN_RE.findall(
                    str(row["content"])
                    .casefold()
                    .replace("_", " ")
                    .replace("-", " ")
                )
            )
            lexical = (
                len(query_tokens & content_tokens) / len(query_tokens)
                if query_tokens
                else 0.0
            )
            stored_vector = [float(value) for value in row["vector"]]
            cosine = sum(
                left * right
                for left, right in zip(
                    query_embedding,
                    stored_vector,
                    strict=True,
                )
            )
            semantic = max(0.0, min(1.0, (cosine + 1.0) / 2.0))
            ranked.append((0.8 * lexical + 0.2 * semantic, row))
        ranked.sort(key=lambda item: (-item[0], str(item[1]["source_uri"])))
        return [
            KnowledgeMatch(
                document_id=str(row["document_id"]),
                title=str(row["title"]),
                source_uri=str(row["source_uri"]),
                ordinal=int(row["ordinal"]),
                content=str(row["content"]),
                score=score,
            )
            for score, row in ranked[:limit]
        ]

    def count_knowledge_documents(self, project_key: str) -> int:
        with self._lock:
            table = self._db.open_table(self._DOCUMENTS)
            return int(
                table.count_rows(
                    filter=f"project_key = {_sql_literal(project_key)}"
                )
            )
