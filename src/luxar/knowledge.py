"""Structured project memory and storage-neutral knowledge services."""

from __future__ import annotations

import dataclasses
import hashlib
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from luxar.database.persistence import KnowledgeMatch, PersistencePort
from luxar.domain.knowledge_atoms import (
    KnowledgeAtom,
    KnowledgeChunk,
    materialize_knowledge_atoms,
    materialize_parameter_atoms,
)
from luxar.model_config import is_local_http_api
from luxar.ports.knowledge_extraction import KnowledgeAtomExtractor


class KnowledgeSettings(BaseSettings):
    """Independent embedding endpoint; it need not be the chat provider."""

    model_config = SettingsConfigDict(
        env_prefix="LUXAR_EMBEDDING_",
        extra="ignore",
    )

    api_key: SecretStr | None = None
    base_url: str = "https://api.openai.com/v1"
    model: str = "text-embedding-3-small"
    dimensions: int = Field(default=1536, ge=1, le=4096)
    timeout_seconds: float = Field(default=30.0, gt=0)

    @property
    def configured(self) -> bool:
        has_key = self.api_key is not None and bool(
            self.api_key.get_secret_value().strip()
        )
        return bool(self.base_url.strip() and self.model.strip()) and (
            has_key or is_local_http_api(self.base_url)
        )

    def sdk_api_key(self) -> str:
        if self.api_key is not None and self.api_key.get_secret_value().strip():
            return self.api_key.get_secret_value().strip()
        if is_local_http_api(self.base_url):
            return "local"
        raise ValueError("未配置 LUXAR_EMBEDDING_API_KEY")


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
        chunks: list[KnowledgeChunk],
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

    def count_all_knowledge_documents(self) -> int: ...

    def list_knowledge_documents(self, project_key: str) -> list[dict[str, object]]: ...

    def get_knowledge_document(
        self, *, project_key: str, document_id: str
    ) -> dict[str, object] | None: ...

    def delete_knowledge_document(
        self, *, project_key: str, document_id: str
    ) -> bool: ...

    def register_entity(
        self, *, entity: object, replace: bool = False
    ) -> bool: ...

    def list_entities(self) -> list[object]: ...

    def find_entity(self, name: str) -> object | None: ...

    def device_tree(self, device: object) -> list[object]: ...

    def search_by_entity(
        self, *, entity_ids: set[str], limit: int = 50
    ) -> list[KnowledgeMatch]: ...

    def entity_candidates(self) -> list[dict[str, object]]: ...


class OpenAIEmbeddingAdapter:
    def __init__(
        self,
        settings: KnowledgeSettings,
        *,
        client: OpenAI | None = None,
    ) -> None:
        if not settings.configured:
            raise ValueError("未配置 LUXAR_EMBEDDING_API_KEY")
        self.dimensions = settings.dimensions
        self._model = settings.model
        self._client = client or OpenAI(
            api_key=settings.sdk_api_key(),
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
        mismatched = next(
            (vector for vector in vectors if len(vector) != self.dimensions),
            None,
        )
        if mismatched is not None:
            raise ValueError(
                "Embedding 服务返回向量维度与配置不一致："
                f"期望 {self.dimensions}，实际 {len(mismatched)}"
            )
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


@dataclass(frozen=True)
class IngestedPdf:
    source_uri: str
    total_pages: int
    batches: int
    documents: list[IngestedDocument]
    knowledge_units: int = 0


class KnowledgeService:
    _MAX_DOCUMENT_BYTES = 2 * 1024 * 1024

    def __init__(
        self,
        index: KnowledgeIndex,
        embeddings: EmbeddingPort,
        *,
        chunk_characters: int = 1400,
        overlap_characters: int = 180,
        atom_extractor: KnowledgeAtomExtractor | None = None,
    ) -> None:
        if chunk_characters < 200:
            raise ValueError("chunk_characters 不能小于 200")
        if overlap_characters < 0 or overlap_characters >= chunk_characters:
            raise ValueError("overlap_characters 必须小于分块长度")
        self._index = index
        self._embeddings = embeddings
        self._chunk_characters = chunk_characters
        self._overlap_characters = overlap_characters
        self._atom_extractor = atom_extractor

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
        # 文档 ID 只由来源 URI 决定：同一来源在任何项目入库都映射到同一篇
        # 全局文档（跨项目去重）。project_key 仅记录来源项目，不参与标识。
        document_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"luxar:{source_uri}")
        )
        records = [
            KnowledgeChunk(
                content=chunk,
                token_count=max(1, len(chunk) // 4),
                embedding=vector,
                metadata={
                    "schema_version": 1,
                    "unit_type": "document_chunk",
                },
            )
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

    def ingest_atoms(
        self,
        *,
        project_key: str,
        source_uri: str,
        title: str,
        atoms: list[KnowledgeAtom],
        content_hash: str,
        metadata: dict[str, object] | None = None,
    ) -> IngestedDocument:
        """Index concrete knowledge statements as one logical source document."""

        if not atoms:
            raise ValueError("文档中没有可写入的具体知识")
        if len(atoms) > 5000:
            raise ValueError("单个文档抽取的知识原子超过 5000 条限制")
        # 入库归属：参数原子按 scope 的 controller/device 名称匹配已注册实体，
        # 命中则把 entity_id 写入原子（检索时可按实体聚合返回）。
        indexed_atoms = [
            self._assign_entity(atom) if atom.category == "parameter" else atom
            for atom in atoms
        ]
        texts = [atom.searchable_text() for atom in indexed_atoms]
        vectors = self._embeddings.embed(texts)
        chunks = [
            KnowledgeChunk(
                content=atom.statement,
                token_count=max(1, len(text) // 4),
                embedding=vector,
                metadata=atom.metadata(),
            )
            for atom, text, vector in zip(
                indexed_atoms, texts, vectors, strict=True
            )
        ]
        document_id = atoms[0].source_document_id
        if not document_id or any(
            atom.source_document_id != document_id for atom in atoms
        ):
            raise ValueError("知识原子的来源文档标识不一致")
        self._index.replace_document(
            document_id=document_id,
            project_key=project_key,
            source_uri=source_uri,
            title=title.strip() or source_uri,
            content_hash=content_hash,
            metadata={
                **(metadata or {}),
                "knowledge_schema_version": 2,
                "knowledge_units": len(atoms),
            },
            chunks=chunks,
        )
        return IngestedDocument(document_id, len(chunks), content_hash)

    def _assign_entity(self, atom: KnowledgeAtom) -> KnowledgeAtom:
        """参数原子按 scope 匹配已注册实体，返回带 entity_id 的副本。

        匹配优先级：device（具体实例）> controller（芯片类）> 其他 scope 值。
        device 优先——同一 controller 下可能有多个模组，具体实例的知识应
        归属到最具体的实体。只匹配已注册实体（find_entity），未命中保持未归属。
        """
        if atom.entity_id:
            return atom
        scope = atom.parameter_scope or {}
        device_name = scope.get("device")
        controller_name = scope.get("controller")
        ordered = []
        if device_name:
            ordered.append(device_name)
        if controller_name:
            ordered.append(controller_name)
        ordered.extend(
            str(value) for key, value in scope.items()
            if key not in {"device", "controller"} and str(value).strip()
        )
        for candidate in ordered:
            entity = self._index.find_entity(str(candidate))
            if entity is not None:
                return KnowledgeAtom(
                    **{
                        **dataclasses.asdict(atom),
                        "entity_id": entity.entity_id,
                    }
                )
        return atom

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
        del project_key
        return self._index.count_all_knowledge_documents()

    def list_documents(self, project_key: str) -> list[dict[str, object]]:
        return self._index.list_knowledge_documents(project_key)

    def get_document(
        self, *, project_key: str, document_id: str
    ) -> dict[str, object] | None:
        return self._index.get_knowledge_document(
            project_key=project_key, document_id=document_id
        )

    def delete_document(self, *, project_key: str, document_id: str) -> bool:
        return self._index.delete_knowledge_document(
            project_key=project_key, document_id=document_id
        )

    # ------------------------------------------------------------------
    # 硬件实体（跨文档聚合同一硬件）
    # ------------------------------------------------------------------

    def register_entity(self, *, entity: object, replace: bool = False) -> bool:
        """注册硬件实体；已存在且 replace=False 拒绝（防误覆盖）。"""
        return self._index.register_entity(entity=entity, replace=replace)

    def list_entities(self) -> list[object]:
        return self._index.list_entities()

    def find_entity(self, name: str) -> object | None:
        return self._index.find_entity(name)

    def device_tree(self, device: object) -> list[object]:
        return self._index.device_tree(device)

    def search_by_entity(
        self,
        *,
        entity_ids: set[str],
        limit: int = 50,
    ) -> list[KnowledgeMatch]:
        """按实体聚合检索：返回归属这些实体的全部原子（不依赖向量）。"""
        return self._index.search_by_entity(
            entity_ids=entity_ids, limit=limit
        )

    def device_knowledge(
        self,
        device: object,
        limit: int = 100,
    ) -> list[KnowledgeMatch]:
        """设备完整知识：device 实体 + 其 chip 链上所有实体的原子聚合。"""
        tree = self._index.device_tree(device)
        return self.search_by_entity(
            entity_ids={item.entity_id for item in tree},
            limit=limit,
        )

    def entity_candidates(self) -> list[dict[str, object]]:
        """扫描已入库原子，返回描述同一硬件的文档组候选（供 agent 提议）。"""
        return self._index.entity_candidates()

    def ingest_pdf(
        self,
        *,
        project_key: str,
        source_uri: str,
        title: str,
        path: Path,
        reader: object | None = None,
        extractor: KnowledgeAtomExtractor | None = None,
        progress_reporter: object | None = None,
    ) -> IngestedPdf:
        """Segment PDFs by chapter, then extract semantic facts with page provenance."""

        from luxar.document_reader import (
            PdfDocumentReader,
            configured_drawing_analyzer,
            iter_pdf_batches,
        )

        active_reader = reader if reader is not None else PdfDocumentReader(
            drawing_analyzer=configured_drawing_analyzer()
        )
        batches = list(iter_pdf_batches(
            active_reader,
            path,
            progress_reporter=progress_reporter,  # type: ignore[arg-type]
        ))
        total_pages = batches[-1].total_pages if batches else 0
        if callable(progress_reporter):
            from luxar.document_reader import PdfReadProgress

            progress_reporter(PdfReadProgress(
                "analyzing",
                total_pages,
                total_pages,
                total_pages or None,
                len(batches),
                "章节划分完成，正在按章节提取具体知识",
            ))
        active_extractor = extractor or self._atom_extractor
        if active_extractor is None:
            from luxar.knowledge_extraction import SemanticKnowledgeAtomExtractor

            active_extractor = SemanticKnowledgeAtomExtractor()
        extraction = active_extractor.extract(
            title=title,
            source_uri=source_uri,
            batches=batches,
        )
        drafts = extraction.atoms
        parameter_drafts = extraction.parameters
        # 文档 ID 只由来源 URI 决定（全局共享知识库，跨项目去重）。
        document_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"luxar:{source_uri}")
        )
        atoms = materialize_knowledge_atoms(
            drafts,
            document_id=document_id,
            source_uri=source_uri,
            source_title=title.strip() or source_uri,
        )
        parameter_atoms = materialize_parameter_atoms(
            parameter_drafts,
            document_id=document_id,
            source_uri=source_uri,
            source_title=title.strip() or source_uri,
        )
        all_atoms = atoms + parameter_atoms
        if callable(progress_reporter):
            progress_reporter(PdfReadProgress(
                "indexing",
                total_pages,
                total_pages,
                total_pages or None,
                len(batches),
                "具体知识提取完成，正在生成向量并写入知识库",
            ))
        source_digest = hashlib.sha256()
        for batch in batches:
            source_digest.update(batch.content.encode("utf-8"))
        content_hash = source_digest.hexdigest()
        document = self.ingest_atoms(
            project_key=project_key,
            source_uri=source_uri,
            title=title,
            atoms=all_atoms,
            content_hash=content_hash,
            metadata={
                "media_type": "application/pdf",
                "source_uri": source_uri,
                "total_pages": total_pages,
                "extraction_sections": len(batches),
                "segmentation": "chapter",
                "parameter_atoms": len(parameter_atoms),
            },
        )

        # Re-importing a legacy PDF upgrades it from per-page-batch documents
        # after the new logical document has been stored successfully.
        legacy_prefix = source_uri + "#pages="
        for existing in self.list_documents(project_key):
            existing_uri = str(existing.get("source_uri", ""))
            existing_id = str(existing.get("document_id", ""))
            if existing_uri.startswith(legacy_prefix) and existing_id != document_id:
                self.delete_document(
                    project_key=project_key,
                    document_id=existing_id,
                )
        if callable(progress_reporter):
            progress_reporter(PdfReadProgress(
                "completed",
                total_pages,
                total_pages,
                total_pages or None,
                len(batches),
                f"PDF 知识入库完成，共 {total_pages} 页、{len(batches)} 个章节单元",
            ))
        return IngestedPdf(
            source_uri,
            total_pages,
            len(batches),
            [document],
            len(all_atoms),
        )


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
            knowledge_result = previous_result.get("knowledge_result")
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
            if isinstance(knowledge_result, dict):
                technical_context = str(
                    knowledge_result.get("technical_context", "")
                ).strip()
                if technical_context:
                    result["previous_completed_run"]["document_context"] = {
                        "title": str(knowledge_result.get("title", ""))[:500],
                        "total_pages": knowledge_result.get("total_pages"),
                        "technical_context": technical_context[:20_000],
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
                    "knowledge_id": item.knowledge_id,
                    "subject": item.subject,
                    "content": item.content,
                    "category": item.category,
                    "applicable_conditions": list(item.applicable_conditions),
                    "limitations": list(item.limitations),
                    "source_pages": list(item.source_pages),
                    "score": item.score,
                }
                for item in matches
            ]
        return result
