from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Optional

import numpy as np

from luxar.models.schemas import DocumentParseResult, KnowledgeChunk


class _EmbeddingModel:
    _instance: Optional["_EmbeddingModel"] = None
    _model = None
    _dimension: int = 512

    def __new__(cls) -> "_EmbeddingModel":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _lazy_load(self) -> None:
        if self._model is not None:
            return
        try:
            import os, logging
            os.environ.setdefault("HF_HUB_DISABLE_SYMPROMPT", "1")
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("BAAI/bge-small-zh-v1.5", device="cpu")
            self._dimension = self._model.get_embedding_dimension()
        except Exception:
            self._model = None

    def embed(self, text: str) -> list[float]:
        self._lazy_load()
        if self._model is None:
            return [0.0] * self._dimension
        result = self._model.encode(text, normalize_embeddings=True)
        return result.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self._lazy_load()
        if self._model is None:
            return [[0.0] * self._dimension for _ in texts]
        results = self._model.encode(texts, normalize_embeddings=True)
        return [r.tolist() for r in results]

    @property
    def dimension(self) -> int:
        self._lazy_load()
        return self._dimension

    @property
    def available(self) -> bool:
        self._lazy_load()
        return self._model is not None


_embedder = _EmbeddingModel()


class KnowledgeBase:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "knowledge.db"
        self._ensure_schema()

    def store_document(self, result: DocumentParseResult) -> DocumentParseResult:
        if not result.success:
            return result
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                INSERT INTO documents (doc_id, source_path, title, summary, chunk_count, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(doc_id) DO UPDATE SET
                    source_path=excluded.source_path,
                    title=excluded.title,
                    summary=excluded.summary,
                    chunk_count=excluded.chunk_count,
                    updated_at=datetime('now')
                """,
                (
                    result.document_id,
                    result.source_path,
                    result.title,
                    result.summary,
                    result.chunk_count,
                ),
            )
            connection.execute("DELETE FROM chunks WHERE doc_id = ?", (result.document_id,))
            chunk_texts = [self._chunk_text_for_embed(chunk) for chunk in result.chunks]
            dense_vectors = _embedder.embed_batch(chunk_texts)
            connection.executemany(
                """
                INSERT INTO chunks (chunk_id, doc_id, source_path, title, content, keywords, vector_json, vector_dense, page_start, page_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.source_path,
                        chunk.title,
                        chunk.content,
                        ",".join(chunk.keywords),
                        json.dumps(self._build_sparse_vector(chunk)),
                        json.dumps(dense_vectors[i]),
                        chunk.page_start,
                        chunk.page_end,
                    )
                    for i, chunk in enumerate(result.chunks)
                ],
            )
            connection.commit()
        return result

    def search(self, query: str, limit: int = 5) -> list[KnowledgeChunk]:
        normalized = query.strip().lower()
        if not normalized:
            return []
        query_vector = self._vectorize_query(normalized)
        if not query_vector:
            return []
        query_dense = _embedder.embed(normalized)
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT chunk_id, doc_id, source_path, title, content, keywords, vector_json, vector_dense, page_start, page_end
                FROM chunks
                """
            ).fetchall()
        scored: list[tuple[float, tuple[object, ...]]] = []
        for row in rows:
            sparse_vector = self._load_vector(str(row[6] or ""))
            lexical_score = self._keyword_overlap_score(query_vector, sparse_vector)
            sparse_cosine = self._cosine_similarity(query_vector, sparse_vector)
            dense_cosine = self._dense_cosine_similarity(query_dense, str(row[7] or ""))
            score = (lexical_score * 0.15) + (sparse_cosine * 0.25) + (dense_cosine * 0.60)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [self._row_to_chunk(row, score=score) for score, row in scored[:limit]]

    def summarize_query(self, query: str, limit: int = 3) -> str:
        chunks = self.search(query=query, limit=limit)
        if not chunks:
            return ""
        lines = []
        for chunk in chunks:
            excerpt = chunk.content[:240].strip()
            lines.append(f"[{chunk.title}] {excerpt}")
        return "\n".join(lines)

    def stats(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            document_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunk_count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {
            "documents": int(document_count),
            "chunks": int(chunk_count),
        }

    def _chunk_text_for_embed(self, chunk: KnowledgeChunk) -> str:
        return " ".join([chunk.title, chunk.content, " ".join(chunk.keywords)])

    def _ensure_schema(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    keywords TEXT NOT NULL DEFAULT '',
                    vector_json TEXT NOT NULL DEFAULT '{}',
                    vector_dense TEXT NOT NULL DEFAULT '[]',
                    page_start INTEGER NOT NULL DEFAULT 0,
                    page_end INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(chunks)").fetchall()
            }
            if "vector_json" not in columns:
                connection.execute("ALTER TABLE chunks ADD COLUMN vector_json TEXT NOT NULL DEFAULT '{}'")
            if "vector_dense" not in columns:
                connection.execute("ALTER TABLE chunks ADD COLUMN vector_dense TEXT NOT NULL DEFAULT '[]'")
            connection.commit()

    def _row_to_chunk(self, row: tuple[object, ...], score: float = 0.0) -> KnowledgeChunk:
        keywords = [item for item in str(row[5]).split(",") if item]
        return KnowledgeChunk(
            chunk_id=str(row[0]),
            doc_id=str(row[1]),
            source_path=str(row[2]),
            title=str(row[3]),
            content=str(row[4]),
            keywords=keywords,
            page_start=int(row[8]),
            page_end=int(row[9]),
            score=score,
        )

    def _build_sparse_vector(self, chunk: KnowledgeChunk) -> dict[str, float]:
        text = " ".join([chunk.title, chunk.content, " ".join(chunk.keywords)])
        return self._vectorize_query(text)

    def _vectorize_query(self, text: str) -> dict[str, float]:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,}", text.lower())
        stop_words = {
            "the", "and", "for", "with", "that", "this", "from", "into", "when",
            "then", "than", "page", "data", "read", "write", "mode", "bits",
            "register", "device",
        }
        counts: dict[str, float] = {}
        for token in tokens:
            if token in stop_words:
                continue
            counts[token] = counts.get(token, 0.0) + 1.0
        return counts

    def _load_vector(self, payload: str) -> dict[str, float]:
        if not payload.strip():
            return {}
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return {str(key): float(value) for key, value in raw.items()}

    def _keyword_overlap_score(self, query_vector: dict[str, float], chunk_vector: dict[str, float]) -> float:
        if not query_vector or not chunk_vector:
            return 0.0
        hits = sum(1 for token in query_vector if token in chunk_vector)
        return hits / max(len(query_vector), 1)

    def _cosine_similarity(self, left: dict[str, float], right: dict[str, float]) -> float:
        if not left or not right:
            return 0.0
        dot = sum(value * right.get(token, 0.0) for token, value in left.items())
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))
        if left_norm == 0.0 or right_norm == 0.0:
            return 0.0
        return dot / (left_norm * right_norm)

    def _dense_cosine_similarity(self, query_vec: list[float], dense_json: str) -> float:
        if not dense_json.strip() or dense_json.strip() == "[]":
            return 0.0
        try:
            chunk_vec = json.loads(dense_json)
        except (json.JSONDecodeError, TypeError):
            return 0.0
        if not chunk_vec or len(chunk_vec) != len(query_vec):
            return 0.0
        q = np.array(query_vec, dtype=np.float32)
        c = np.array(chunk_vec, dtype=np.float32)
        dot = float(np.dot(q, c))
        return dot

