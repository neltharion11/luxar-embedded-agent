"""Embedded LanceDB knowledge index used by the local storage profile."""

from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path
from typing import Any

from luxar.database.persistence import KnowledgeMatch
from luxar.domain.hardware_entities import HardwareEntity
from luxar.domain.knowledge_atoms import KnowledgeChunk


_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_SDK_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
#: 精确标识符：十六进制字面量（0x8D）或含数字的寄存器/引脚名（GPIO21、REG0xF4）。
#: 词法命中权重加倍——这类 token 是参数型知识召回的关键锚点。
_HEX_TOKEN_RE = re.compile(r"^(?:0[xX][0-9A-Fa-f]+|[A-Za-z]+[0-9]+[A-Za-z0-9_]*|[A-Za-z_]+0[xX][0-9A-Fa-f]+)$")


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class LanceDBKnowledgeIndex:
    """Persist documents and vectors locally without a database server."""

    _DOCUMENTS = "luxar_knowledge_documents"
    _CHUNKS = "luxar_knowledge_chunks"
    _ENTITIES = "luxar_hardware_entities"

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

        def create_chunks_table() -> None:
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
                        pa.field("metadata_json", pa.string()),
                        pa.field(
                            "vector",
                            pa.list_(pa.float32(), self.dimensions),
                        ),
                    ]
                ),
            )

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
        if self._ENTITIES not in names:
            self._db.create_table(
                self._ENTITIES,
                schema=pa.schema(
                    [
                        pa.field("entity_id", pa.string()),
                        pa.field("kind", pa.string()),
                        pa.field("name", pa.string()),
                        pa.field("chip_ref", pa.string()),
                        pa.field("source_uris", pa.string()),
                        pa.field("aliases", pa.string()),
                        pa.field("notes", pa.string()),
                    ]
                ),
            )
        if self._CHUNKS not in names:
            create_chunks_table()
        else:
            chunk_table = self._db.open_table(self._CHUNKS)
            vector_type = chunk_table.schema.field("vector").type
            stored_dimensions = getattr(vector_type, "list_size", None)
            if stored_dimensions != self.dimensions:
                document_table = self._db.open_table(self._DOCUMENTS)
                if (
                    chunk_table.count_rows() == 0
                    and document_table.count_rows() == 0
                ):
                    self._db.drop_table(self._CHUNKS)
                    create_chunks_table()
                else:
                    raise ValueError(
                        "Embedding 向量维度与现有知识库不一致；"
                        "切换模型前需要重新索引知识文档"
                    )
            elif "metadata_json" not in chunk_table.schema.names:
                # LanceDB schemas are immutable across older releases. Preserve
                # existing vectors while adding atom metadata required by v2.
                legacy_rows = chunk_table.to_arrow().to_pylist()
                self._db.drop_table(self._CHUNKS)
                create_chunks_table()
                if legacy_rows:
                    migrated = [
                        {**row, "metadata_json": "{}"}
                        for row in legacy_rows
                    ]
                    self._db.open_table(self._CHUNKS).add(migrated)

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
        chunks: list[KnowledgeChunk | tuple[str, int, list[float]]],
    ) -> None:
        normalized_chunks = [
            chunk
            if isinstance(chunk, KnowledgeChunk)
            else KnowledgeChunk(
                content=chunk[0],
                token_count=chunk[1],
                embedding=chunk[2],
            )
            for chunk in chunks
        ]
        for chunk in normalized_chunks:
            if len(chunk.embedding) != self.dimensions:
                raise ValueError("知识向量维度与 LanceDB 配置不一致")
            if any(not math.isfinite(value) for value in chunk.embedding):
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
            if normalized_chunks:
                chunk_table.add(
                    [
                        {
                            "chunk_id": f"{document_id}:{ordinal}",
                            "document_id": document_id,
                            "project_key": project_key,
                            "source_uri": source_uri,
                            "title": title,
                            "ordinal": ordinal,
                            "content": chunk.content,
                            "token_count": chunk.token_count,
                            "metadata_json": json.dumps(
                                chunk.metadata,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            "vector": chunk.embedding,
                        }
                        for ordinal, chunk in enumerate(normalized_chunks)
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
                    "metadata_json": "{}",
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
        overlap = query_tokens & content_tokens
        if not overlap:
            return 0.0
        # 十六进制/寄存器类精确标识符（0x8D、GPIO21 等）权重加倍：
        # 这类 token 对向量检索不敏感，但词法精确命中几乎等于"找到了正确条目"
        # （参数型原子的 init 序列、寄存器地址召回的关键）。
        weighted = sum(
            2.0 if _HEX_TOKEN_RE.match(token) else 1.0
            for token in overlap
        )
        return weighted / len(query_tokens)

    @staticmethod
    def _parameter_bonus(query: str, row: dict[str, Any]) -> float:
        """参数原子的结构化加权。

        参数型知识（category="parameter"）的 value/parameter_scope 是精确数据：
        查询中的标识符（0xAE、SPI、2.4-3.5V 等）若命中参数原子，得分应显著高于
        命中文案散文——否则参数原子存在也排不上 top1（SH1106 手册实测）。

        参数原子的 searchable 文本含"参数名+范围+值+原文摘录"，中文命令名
        （"显示开关"）在原文摘录里，hex 标识符在值里。因此基于词法重叠加权即可：
        - 无词法命中（完全无关查询）：不加分，防过度倾斜；
        - 词法命中：按重叠度 +0.05~+0.3，hex 命中值加倍。
        """
        try:
            metadata = json.loads(str(row.get("metadata_json", "{}")))
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict) or metadata.get("category") != "parameter":
            return 0.0
        content = str(row.get("content", ""))
        lexical = LanceDBKnowledgeIndex._lexical_score(query, content)
        if lexical <= 0.0:
            return 0.0
        # 结构化字段（值/范围）命中 hex 标识符：主要加分（精确命中≈答案）
        value = str(metadata.get("parameter_value", ""))
        scope = " ".join(str(v) for v in (metadata.get("parameter_scope") or {}).values())
        query_tokens = {token.casefold() for token in _TOKEN_RE.findall(query)}
        hex_hits = sum(
            1
            for token in query_tokens
            if _HEX_TOKEN_RE.match(token) and token in f"{value} {scope}".casefold()
        )
        if hex_hits:
            return 0.25
        # 无 hex 命中：只给小幅结构加成（词法重叠已说明相关性，但不足以翻转无关查询）
        return 0.05

    def search_knowledge(
        self,
        *,
        project_key: str,
        query_text: str,
        query_embedding: list[float],
        limit: int = 6,
    ) -> list[KnowledgeMatch]:
        """检索整个共享知识库，不做项目过滤。

        知识库存放的是通用知识：任意项目入库的文档都可以被所有项目检索。
        ``project_key`` 仅作为调用方提供的来源提示保留在签名中，不再参与过滤。
        """
        del project_key
        if not 1 <= limit <= 100:
            raise ValueError("limit 必须在 1 到 100 之间")
        if len(query_embedding) != self.dimensions:
            raise ValueError("查询向量维度与 LanceDB 配置不一致")
        with self._lock:
            table = self._db.open_table(self._CHUNKS)
            try:
                rows: list[dict[str, Any]] = (
                    table.search(query_embedding)
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
            base = 0.8 * semantic + 0.2 * lexical
            ranked.append((base + self._parameter_bonus(query_text, row), row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [self._match(row, score) for score, row in ranked[:limit]]

    def search_by_entity(
        self,
        *,
        entity_ids: set[str],
        limit: int = 50,
    ) -> list[KnowledgeMatch]:
        """按实体聚合检索：返回归属这些实体的全部原子（不依赖向量）。

        供"给定硬件实体，取齐 device + chip 链上的知识"使用——驱动编写前
        需要同一硬件的完整参数表，而非单条语义命中。
        """
        if not entity_ids:
            return []
        with self._lock:
            rows = self._db.open_table(self._CHUNKS).to_arrow().to_pylist()
        matches: list[KnowledgeMatch] = []
        for row in rows:
            raw_metadata = row.get("metadata_json", "{}")
            try:
                metadata = json.loads(str(raw_metadata))
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            entity_id = str(metadata.get("entity_id", "")) if isinstance(metadata, dict) else ""
            if entity_id in entity_ids:
                matches.append(self._match(row, 1.0))
        matches.sort(key=lambda item: str(item.subject or ""))
        return matches[:limit]

    @staticmethod
    def _match(row: dict[str, Any], score: float) -> KnowledgeMatch:
        raw_metadata = row.get("metadata_json", "{}")
        try:
            metadata = json.loads(str(raw_metadata))
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        pages = metadata.get("source_pages", [])
        conditions = metadata.get("applicable_conditions", [])
        limitations = metadata.get("limitations", [])
        return KnowledgeMatch(
            document_id=str(row["document_id"]),
            title=str(row["title"]),
            source_uri=str(row["source_uri"]),
            ordinal=int(row["ordinal"]),
            content=str(row["content"]),
            score=score,
            knowledge_id=(
                str(metadata["knowledge_id"])
                if metadata.get("knowledge_id")
                else None
            ),
            subject=(str(metadata["subject"]) if metadata.get("subject") else None),
            category=(
                str(metadata["category"]) if metadata.get("category") else None
            ),
            source_pages=tuple(
                int(page) for page in pages if isinstance(page, int) and page > 0
            ) if isinstance(pages, list) else (),
            source_section=(
                str(metadata["source_section"])
                if metadata.get("source_section")
                else None
            ),
            applicable_conditions=tuple(
                str(value) for value in conditions if isinstance(value, str)
            ) if isinstance(conditions, list) else (),
            limitations=tuple(
                str(value) for value in limitations if isinstance(value, str)
            ) if isinstance(limitations, list) else (),
            metadata={str(key): value for key, value in metadata.items()},
            entity_id=str(metadata.get("entity_id", "")) if metadata.get("entity_id") else "",
        )

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
        return [self._match(row, score) for score, row in ranked[:limit]]

    def count_knowledge_documents(self, project_key: str) -> int:
        """按作用域计数（SDK 知识库按版本作用域使用；项目知识库用全局计数）。"""
        with self._lock:
            table = self._db.open_table(self._DOCUMENTS)
            return int(
                table.count_rows(
                    filter=f"project_key = {_sql_literal(project_key)}"
                )
            )

    def count_all_knowledge_documents(self) -> int:
        """整个共享知识库的文档总数，不按项目过滤。"""
        with self._lock:
            table = self._db.open_table(self._DOCUMENTS)
            return int(table.count_rows())

    def list_knowledge_documents(self, project_key: str) -> list[dict[str, object]]:
        del project_key
        with self._lock:
            rows = self._db.open_table(self._DOCUMENTS).to_arrow().to_pylist()
        result: list[dict[str, object]] = []
        for row in rows:
            result.append({
                "document_id": str(row["document_id"]),
                "source_uri": str(row["source_uri"]),
                "title": str(row["title"]),
                "content_hash": str(row["content_hash"]),
                "metadata": json.loads(str(row["metadata_json"]) or "{}"),
            })
        return sorted(result, key=lambda item: str(item["source_uri"]))

    def get_knowledge_document(
        self, *, project_key: str, document_id: str
    ) -> dict[str, object] | None:
        del project_key
        documents = self.list_knowledge_documents("")
        document = next(
            (item for item in documents if item["document_id"] == document_id), None
        )
        if document is None:
            return None
        with self._lock:
            rows = self._db.open_table(self._CHUNKS).to_arrow().to_pylist()
        chunks = sorted(
            (
                {"ordinal": int(row["ordinal"]), "content": str(row["content"])}
                for row in rows
                if str(row["document_id"]) == document_id
            ),
            key=lambda item: int(item["ordinal"]),
        )
        return {**document, "chunks": chunks}

    def delete_knowledge_document(
        self, *, project_key: str, document_id: str
    ) -> bool:
        del project_key
        document = self.get_knowledge_document(
            project_key="", document_id=document_id
        )
        if document is None:
            return False
        scope = f"document_id = {_sql_literal(document_id)}"
        with self._lock:
            self._db.open_table(self._CHUNKS).delete(scope)
            self._db.open_table(self._DOCUMENTS).delete(scope)
        return True

    # ------------------------------------------------------------------
    # 硬件实体（chip / device 两层，跨文档聚合）
    # ------------------------------------------------------------------

    def register_entity(
        self,
        *,
        entity: HardwareEntity,
        replace: bool = False,
    ) -> bool:
        """注册一个硬件实体。已存在且 replace=False 时拒绝（防误覆盖）。

        返回 True 表示注册成功；False 表示已存在且未覆盖。
        注册成功后自动重挂：把已入库、scope 匹配本实体且未归属的孤儿原子
        补上 entity_id（实体晚于文档入库时无需重导文档）。
        """
        from luxar.domain.hardware_entities import HardwareEntity

        if not isinstance(entity, HardwareEntity):
            raise TypeError("entity 必须是 HardwareEntity")
        if entity.kind not in {"chip", "device"}:
            raise ValueError(f"未知实体类型：{entity.kind}")
        if entity.kind == "device" and not entity.chip_ref:
            raise ValueError("device 实体必须引用一个 chip 实体（chip_ref）")
        with self._lock:
            table = self._db.open_table(self._ENTITIES)
            existing = table.to_arrow().to_pylist()
            if any(str(row["entity_id"]) == entity.entity_id for row in existing):
                if not replace:
                    return False
                self._db.open_table(self._ENTITIES).delete(
                    f"entity_id = {_sql_literal(entity.entity_id)}"
                )
            table.add([entity.to_row()])
        self._reattach_orphans(entity)
        return True

    def _reattach_orphans(self, entity: HardwareEntity) -> int:
        """把已入库、scope 匹配本实体且未归属的原子补上 entity_id。

        匹配：原子的 parameter_scope 中任一键值（controller/device/其他）
        与实体名称/别名一致，且当前 entity_id 为空。返回重挂数量。
        与入库归属同一优先级：device 实例 > controller 芯片类（同一原子
        同时声明 device+controller 时归属 device）。
        """
        entity_names = entity.match_names
        with self._lock:
            chunk_table = self._db.open_table(self._CHUNKS)
            rows = chunk_table.to_arrow().to_pylist()
            updates: list[dict[str, object]] = []
            for row in rows:
                try:
                    metadata = json.loads(str(row.get("metadata_json", "{}")))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(metadata, dict):
                    continue
                if metadata.get("entity_id"):
                    continue  # 已归属
                scope = metadata.get("parameter_scope")
                if not isinstance(scope, dict):
                    continue
                # chip 实体只挂"无 device 声明"的原子（有 device 声明的原子
                # 属于具体模组，应留给 device 实体——即使 chip 先注册也不能抢）
                if entity.kind == "chip" and scope.get("device"):
                    continue
                # 按优先级取第一个与实体匹配的 scope 值
                device_value = scope.get("device")
                controller_value = scope.get("controller")
                ordered = []
                if device_value:
                    ordered.append(str(device_value))
                if controller_value:
                    ordered.append(str(controller_value))
                ordered.extend(
                    str(value) for key, value in scope.items()
                    if key not in {"device", "controller"} and str(value).strip()
                )
                matched = next(
                    (value for value in ordered if value.strip().casefold() in entity_names),
                    None,
                )
                if matched is None:
                    continue
                updated_metadata = {**metadata, "entity_id": entity.entity_id}
                chunk_table.update(
                    where=f"chunk_id = {_sql_literal(str(row['chunk_id']))}",
                    values={
                        "metadata_json": json.dumps(
                            updated_metadata,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    },
                )
                updates.append(row)
        return len(updates)

    def list_entities(self) -> list[HardwareEntity]:
        """列出全部硬件实体。"""
        from luxar.domain.hardware_entities import HardwareEntity

        with self._lock:
            rows = self._db.open_table(self._ENTITIES).to_arrow().to_pylist()
        entities: list[HardwareEntity] = []
        for row in rows:
            entities.append(
                HardwareEntity(
                    entity_id=str(row["entity_id"]),
                    kind=str(row["kind"]),  # type: ignore[arg-type]
                    name=str(row["name"]),
                    chip_ref=str(row["chip_ref"]) or None,
                    source_uris=tuple(
                        uri for uri in str(row["source_uris"]).split(",") if uri
                    ),
                    aliases=tuple(
                        alias for alias in str(row["aliases"]).split(",") if alias
                    ),
                    notes=str(row["notes"]),
                )
            )
        return entities

    def find_entity(self, name: str) -> HardwareEntity | None:
        """按名称/别名/实体 id 匹配实体（大小写不敏感）。"""
        key = name.strip().casefold()
        for entity in self.list_entities():
            if key in entity.match_names:
                return entity
        return None

    def device_tree(self, device: HardwareEntity) -> list[HardwareEntity]:
        """返回 device + 其引用的 chip 链（沿 chip_ref 向上）。"""
        if device.kind != "device":
            return [device]
        entities = {item.entity_id: item for item in self.list_entities()}
        result = [device]
        cursor = device
        while cursor.chip_ref and cursor.chip_ref in entities:
            chip = entities[cursor.chip_ref]
            result.append(chip)
            cursor = chip
        return result

    def entity_candidates(self) -> list[dict[str, object]]:
        """扫描已入库参数原子，找出描述同一硬件的文档组（关联候选）。

        按 parameter_scope 的 controller/device 值分组：同一 scope 值被
        多个文档声明 → 这些文档很可能描述同一硬件（如芯片手册 + 屏厂手册）。
        返回 [{"scope_value": "SH1106", "kind": "controller", "documents": [...]}]。
        只读探测，供 agent 提议、用户确认。
        """
        with self._lock:
            rows = self._db.open_table(self._CHUNKS).to_arrow().to_pylist()
        groups: dict[tuple[str, str], set[str]] = {}
        doc_titles: dict[str, str] = {}
        for row in rows:
            try:
                metadata = json.loads(str(row.get("metadata_json", "{}")))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, dict):
                continue
            scope = metadata.get("parameter_scope")
            if not isinstance(scope, dict):
                continue
            for key, value in scope.items():
                if key not in {"controller", "device"}:
                    continue
                value_text = str(value).strip().casefold()
                if not value_text:
                    continue
                source_uri = str(row.get("source_uri", ""))
                groups.setdefault((key, value_text), set()).add(source_uri)
                doc_titles.setdefault(
                    source_uri, str(row.get("title", source_uri))
                )
        candidates: list[dict[str, object]] = []
        for (key, value), uris in sorted(groups.items()):
            if len(uris) < 2:
                continue  # 单文档不构成"多份描述同一硬件"
            candidates.append(
                {
                    "scope_key": key,
                    "scope_value": value,
                    "documents": [
                        {"source_uri": uri, "title": doc_titles.get(uri, uri)}
                        for uri in sorted(uris)
                    ],
                }
            )
        return candidates
