"""Structured project memory and storage-neutral knowledge services."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from luxar.database.persistence import KnowledgeMatch, PersistencePort


class KnowledgeSettings(BaseSettings):
    """Independent embedding endpoint; it need not be the chat provider."""

    model_config = SettingsConfigDict(
        env_prefix="LUXAR_EMBEDDING_",
        extra="ignore",
    )

    api_key: SecretStr | None = None
    base_url: str = "https://api.openai.com/v1"
    model: str = "text-embedding-3-small"
    dimensions: int = Field(default=1536, ge=1, le=1536)
    timeout_seconds: float = Field(default=30.0, gt=0)

    @property
    def configured(self) -> bool:
        return self.api_key is not None and bool(
            self.api_key.get_secret_value().strip()
        )


class EmbeddingPort(Protocol):
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class KnowledgeIndex(Protocol):
    """Vector/document index boundary; application records do not implement it."""

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
    ) -> None: ...

    def search_knowledge(
        self,
        *,
        project_key: str,
        query_text: str,
        query_embedding: list[float],
        limit: int = 6,
    ) -> list[KnowledgeMatch]: ...

    def count_knowledge_documents(self, project_key: str) -> int: ...


class OpenAIEmbeddingAdapter:
    def __init__(
        self,
        settings: KnowledgeSettings,
        *,
        client: OpenAI | None = None,
    ) -> None:
        if not settings.configured or settings.api_key is None:
            raise ValueError("未配置 LUXAR_EMBEDDING_API_KEY")
        self.dimensions = settings.dimensions
        self._model = settings.model
        self._client = client or OpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            max_retries=0,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(
            model=self._model,
            input=texts,
            dimensions=self.dimensions,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [list(item.embedding) for item in ordered]
        if len(vectors) != len(texts):
            raise RuntimeError("embedding 返回数量不匹配")
        return vectors


class LocalHashEmbeddingAdapter:
    """Small deterministic embedding for local SDK-document retrieval.

    This is deliberately independent from the project knowledge embedding
    provider. It uses feature hashing, needs no model download, and keeps the
    Windows local profile usable offline.
    """

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 32:
            raise ValueError("本地 embedding 维度不能小于 32")
        self.dimensions = dimensions

    @staticmethod
    def _tokens(text: str) -> list[str]:
        normalized = text.casefold().replace("_", " ").replace("-", " ")
        english = re.findall(r"[a-z0-9]{2,}", normalized)
        chinese = re.findall(r"[\u4e00-\u9fff]", normalized)
        chinese_bigrams = [
            "".join(chinese[index : index + 2])
            for index in range(max(0, len(chinese) - 1))
        ]
        return english + chinese + chinese_bigrams

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in self._tokens(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


@dataclass(frozen=True)
class IngestedDocument:
    document_id: str
    chunks: int
    content_hash: str


class KnowledgeService:
    _MAX_DOCUMENT_BYTES = 2 * 1024 * 1024

    def __init__(
        self,
        index: KnowledgeIndex,
        embeddings: EmbeddingPort,
        *,
        chunk_characters: int = 1400,
        overlap_characters: int = 180,
    ) -> None:
        if chunk_characters < 200:
            raise ValueError("chunk_characters 不能小于 200")
        if overlap_characters < 0 or overlap_characters >= chunk_characters:
            raise ValueError("overlap_characters 必须小于分块长度")
        self._index = index
        self._embeddings = embeddings
        self._chunk_characters = chunk_characters
        self._overlap_characters = overlap_characters

    def _chunks(self, content: str) -> list[str]:
        normalized = re.sub(r"\r\n?", "\n", content).strip()
        if not normalized:
            raise ValueError("知识文档内容不能为空")
        if len(normalized.encode("utf-8")) > self._MAX_DOCUMENT_BYTES:
            raise ValueError("知识文档超过 2 MiB 限制")
        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(start + self._chunk_characters, len(normalized))
            if end < len(normalized):
                boundary = normalized.rfind("\n", start + 200, end)
                if boundary > start:
                    end = boundary
            chunks.append(normalized[start:end].strip())
            if end >= len(normalized):
                break
            start = max(end - self._overlap_characters, start + 1)
        return [chunk for chunk in chunks if chunk]

    def ingest(
        self,
        *,
        project_key: str,
        source_uri: str,
        title: str,
        content: str,
        metadata: dict[str, object] | None = None,
    ) -> IngestedDocument:
        chunks = self._chunks(content)
        vectors = self._embeddings.embed(chunks)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        document_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"luxar:{project_key}:{source_uri}")
        )
        records = [
            (chunk, max(1, len(chunk) // 4), vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self._index.replace_document(
            document_id=document_id,
            project_key=project_key,
            source_uri=source_uri,
            title=title.strip() or source_uri,
            content_hash=digest,
            metadata=metadata or {},
            chunks=records,
        )
        return IngestedDocument(document_id, len(chunks), digest)

    def search(
        self,
        *,
        project_key: str,
        query: str,
        limit: int = 6,
    ) -> list[KnowledgeMatch]:
        if not query.strip():
            return []
        vector = self._embeddings.embed([query])[0]
        return self._index.search_knowledge(
            project_key=project_key,
            query_text=query,
            query_embedding=vector,
            limit=limit,
        )

    def document_count(self, project_key: str) -> int:
        return self._index.count_knowledge_documents(project_key)


class ProjectContextProvider:
    """Build bounded, source-labelled context for one requirement request."""

    def __init__(
        self,
        persistence: PersistencePort,
        project_key: str,
        knowledge: KnowledgeService | None = None,
    ) -> None:
        self._persistence = persistence
        self._project_key = project_key
        self._knowledge = knowledge

    def __call__(self, task_text: str) -> dict[str, object]:
        memories = self._persistence.find_memories(
            self._project_key,
            limit=20,
        )
        result: dict[str, object] = {
            "memories": [
                {
                    "key": item.memory_key,
                    "type": item.memory_type,
                    "value": item.value,
                    "confidence": item.confidence,
                }
                for item in memories
            ]
        }
        previous = self._persistence.get_latest_completed_run(
            self._project_key
        )
        if previous is not None:
            previous_result = previous.result
            analysis = previous_result.get("project_analysis")
            build = previous_result.get("build_evidence")
            flash = previous_result.get("flash_evidence")
            result["previous_completed_run"] = {
                "task_text": previous.task_text[:2000],
                "requirement": previous_result.get("requirement"),
                "project_summary": (
                    analysis.get("summary")
                    if isinstance(analysis, dict)
                    else None
                ),
                "changed_files": previous_result.get("changed_files", []),
                "reference_examples": previous_result.get(
                    "reference_examples", []
                ),
                "build_evidence": (
                    {
                        "success": build.get("success"),
                        "return_code": build.get("return_code"),
                        "error_category": build.get("error_category"),
                    }
                    if isinstance(build, dict)
                    else None
                ),
                "flash_evidence": (
                    {
                        "success": flash.get("success"),
                        "return_code": flash.get("return_code"),
                        "port": flash.get("port"),
                        "error_category": flash.get("error_category"),
                    }
                    if isinstance(flash, dict)
                    else None
                ),
                "device_diagnosis": previous_result.get(
                    "device_diagnosis"
                ),
            }
        result["recent_conversation"] = [
            {
                "role": item.get("role", "")[:20],
                "content": item.get("content", "")[:2000],
            }
            for item in self._persistence.get_messages(self._project_key)[-8:]
            if item.get("role") in {"user", "assistant"}
        ]
        if self._knowledge is not None:
            matches = self._knowledge.search(
                project_key=self._project_key,
                query=task_text,
            )
            result["knowledge"] = [
                {
                    "title": item.title,
                    "source_uri": item.source_uri,
                    "content": item.content,
                    "score": item.score,
                }
                for item in matches
            ]
        return result
