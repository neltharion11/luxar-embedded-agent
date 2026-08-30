from __future__ import annotations

from pathlib import Path

import pytest

from luxar.document_reader import PdfBatch
from luxar.domain.hardware_entities import HardwareEntity, entity_id_for
from luxar.domain.knowledge_atoms import KnowledgeAtomDraft
from luxar.knowledge import KnowledgeService, LocalHashEmbeddingAdapter
from luxar.knowledge_extraction import SemanticKnowledgeAtomExtractor
from luxar.lance_knowledge import LanceDBKnowledgeIndex
from luxar.ports.knowledge_extraction import KnowledgeExtraction


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


class ParameterReader:
    """返回含 SH1106 init 序列章节的批次，供参数型原子测试。"""

    def iter_batches(self, path: Path):
        del path
        yield PdfBatch(
            12,
            13,
            2,
            "## 第 12 页\n### 初始化序列\nSH1106 Initialization Sequence: "
            "0xAE 0xD5 0x80 0xA8 0x3F 0x40 0xDA 0x12 0xAF",
            False,
            section_title="初始化序列",
            section_path=("命令表", "初始化序列"),
            section_level=2,
        )


class ParameterExtractor:
    """固定输出参数型原子（模拟模型抽取），走逐字校验后入库。"""

    def __init__(self, excerpt: str):
        self._excerpt = excerpt

    def extract(self, *, title, source_uri, batches):
        from luxar.domain.knowledge_atoms import ParameterAtomDraft
        from luxar.ports.knowledge_extraction import KnowledgeExtraction

        del title, source_uri
        return KnowledgeExtraction(
            atoms=[],
            parameters=[
                ParameterAtomDraft(
                    parameter="init_sequence",
                    value_type="bytes",
                    value="0xAE 0xD5 0x80 0xA8 0x3F 0x40 0xDA 0x12 0xAF",
                    scope={"controller": "sh1106"},
                    source_pages=[12],
                    source_excerpt=self._excerpt,
                    confidence=0.95,
                )
            ],
        )


def test_ingest_pdf_stores_parameter_atoms_with_scope(tmp_path: Path) -> None:
    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    imported = service.ingest_pdf(
        project_key="0:oled",
        source_uri="docs/sh1106.pdf",
        title="SH1106 数据手册",
        path=tmp_path / "not-opened.pdf",
        reader=ParameterReader(),
        extractor=ParameterExtractor(
            "0xAE 0xD5 0x80 0xA8 0x3F 0x40 0xDA 0x12 0xAF"
        ),
    )
    assert imported.knowledge_units == 1
    # 按参数名+scope 召回
    matches = service.search(
        project_key="0:oled",
        query="SH1106 init_sequence 初始化",
        limit=4,
    )
    assert matches
    top = matches[0]
    assert top.category == "parameter"
    metadata = top.metadata or {}
    assert metadata.get("parameter_scope") == {"controller": "sh1106"}
    assert metadata.get("parameter_value") == (
        "0xAE 0xD5 0x80 0xA8 0x3F 0x40 0xDA 0x12 0xAF"
    )


def test_hex_token_lexical_weight_boosts_parameter_recall(tmp_path: Path) -> None:
    """查询含十六进制标识符时词法命中加权，参数型原子应排在前面。"""
    from luxar.lance_knowledge import LanceDBKnowledgeIndex

    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    service.ingest_pdf(
        project_key="0:oled",
        source_uri="docs/sh1106.pdf",
        title="SH1106 数据手册",
        path=tmp_path / "not-opened.pdf",
        reader=ParameterReader(),
        extractor=ParameterExtractor(
            "0xAE 0xD5 0x80 0xA8 0x3F 0x40 0xDA 0x12 0xAF"
        ),
    )
    # 带精确字节查询：应命中参数原子
    matches = service.search(
        project_key="0:oled",
        query="0xD5 0x80 SH1106",
        limit=4,
    )
    assert matches
    assert any(m.category == "parameter" for m in matches)


def test_parameter_bonus_ranks_parameter_above_prose(tmp_path: Path) -> None:
    """参数原子召回加权：hex 命中参数值时参数原子应排在散文原子之前。"""
    from luxar.domain.knowledge_atoms import ParameterAtomDraft

    class MixedExtractor:
        """同时产出散文原子与参数原子，二者都含 0xAE 关键词。"""

        def extract(self, *, title, source_uri, batches):
            del title, source_uri
            prose = KnowledgeAtomDraft(
                subject="SH1106 显示命令",
                statement="SH1106 通过 0xAE 命令关闭显示，0xAF 打开显示。",
                source_pages=[12],
                source_excerpt="SH1106 通过 0xAE 命令关闭显示",
            )
            param = ParameterAtomDraft(
                parameter="command_display_on_off",
                value_type="bytes",
                value="0xAE 0xAF",
                scope={"controller": "SH1106"},
                source_pages=[12],
                source_excerpt="显示开关命令：0xAE 0xAF",
            )
            return KnowledgeExtraction(atoms=[prose], parameters=[param])

    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    service.ingest_pdf(
        project_key="0:oled",
        source_uri="docs/sh1106.pdf",
        title="SH1106 数据手册",
        path=tmp_path / "not-opened.pdf",
        reader=ParameterReader(),
        extractor=MixedExtractor(),
    )
    matches = service.search(
        project_key="0:oled",
        query="SH1106 显示开关命令 0xAE",
        limit=4,
    )
    assert matches
    # 参数原子（category=parameter）应排在第一
    assert matches[0].category == "parameter"
    assert matches[0].subject == "command_display_on_off"


def test_parameter_bonus_does_not_boost_unrelated_query(tmp_path: Path) -> None:
    """无关查询（无 hex、无词法命中）不得因参数原子存在而被抬升。"""
    from luxar.domain.knowledge_atoms import ParameterAtomDraft

    class BrightnessExtractor:
        """散文原子含"亮度"关键词，参数原子与亮度无关。"""

        def extract(self, *, title, source_uri, batches):
            del title, source_uri
            prose = KnowledgeAtomDraft(
                subject="SH1106 亮度调节",
                statement="SH1106 亮度可通过对比度寄存器调节。",
                source_pages=[12],
                source_excerpt="SH1106 亮度可通过对比度寄存器调节",
            )
            param = ParameterAtomDraft(
                parameter="command_display_on_off",
                value_type="bytes",
                value="0xAE 0xAF",
                scope={"controller": "SH1106"},
                source_pages=[12],
                source_excerpt="显示开关命令：0xAE 0xAF",
            )
            return KnowledgeExtraction(atoms=[prose], parameters=[param])

    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    service.ingest_pdf(
        project_key="0:oled",
        source_uri="docs/sh1106.pdf",
        title="SH1106 数据手册",
        path=tmp_path / "not-opened.pdf",
        reader=ParameterReader(),
        extractor=BrightnessExtractor(),
    )
    matches = service.search(
        project_key="0:oled",
        query="SH1106 亮度调节",
        limit=4,
    )
    assert matches
    # 亮度查询应命中散文原子（相关），而非无关的显示开关参数原子
    assert matches[0].subject == "SH1106 亮度调节"


# ---------------------------------------------------------------------------
# 硬件实体（chip / device 两层，跨文档聚合）
# ---------------------------------------------------------------------------


def test_entity_id_is_deterministic_and_kind_scoped() -> None:
    chip = entity_id_for("chip", "SH1106")
    device = entity_id_for("device", "1.3寸横屏")
    assert chip.startswith("chip-")
    assert device.startswith("device-")
    # 同名同类确定性；不同类不同 id
    assert entity_id_for("chip", "SH1106") == chip
    assert entity_id_for("chip", "1.3寸横屏") != device


def test_register_and_find_entity(tmp_path: Path) -> None:
    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    chip = HardwareEntity(
        entity_id=entity_id_for("chip", "SH1106"),
        kind="chip",
        name="SH1106",
        source_uris=("docs/sh1106.pdf",),
        aliases=("sh1106", "sino wealth sh1106"),
    )
    assert service.register_entity(entity=chip) is True
    # 重复注册被拒绝（防误覆盖）
    assert service.register_entity(entity=chip) is False
    # 按名称/别名匹配
    assert service.find_entity("SH1106") == chip
    assert service.find_entity("sh1106") == chip
    assert service.find_entity("Sino Wealth SH1106") == chip
    assert service.find_entity("nope") is None


def test_device_requires_chip_ref_and_tree_resolves(tmp_path: Path) -> None:
    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    chip = HardwareEntity(
        entity_id=entity_id_for("chip", "SH1106"),
        kind="chip",
        name="SH1106",
        source_uris=("docs/sh1106.pdf",),
    )
    service.register_entity(entity=chip)
    # device 无 chip_ref 被拒
    bad = HardwareEntity(
        entity_id=entity_id_for("device", "1.3寸横屏"),
        kind="device",
        name="1.3寸横屏",
    )
    with pytest.raises(ValueError):
        service.register_entity(entity=bad)
    # 合法 device 注册 + 树解析
    device = HardwareEntity(
        entity_id=entity_id_for("device", "1.3寸横屏"),
        kind="device",
        name="1.3寸横屏",
        chip_ref=chip.entity_id,
        source_uris=("docs/panel.pdf",),
    )
    assert service.register_entity(entity=device) is True
    tree = service.device_tree(device)
    assert [item.kind for item in tree] == ["device", "chip"]
    assert tree[1].name == "SH1106"


def test_entities_persist_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.lance"
    service = KnowledgeService(
        LanceDBKnowledgeIndex(path, dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    chip = HardwareEntity(
        entity_id=entity_id_for("chip", "SH1106"),
        kind="chip",
        name="SH1106",
    )
    service.register_entity(entity=chip)
    # 重新打开索引，实体仍在
    reopened = KnowledgeService(
        LanceDBKnowledgeIndex(path, dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    entities = reopened.list_entities()
    assert len(entities) == 1
    assert entities[0].name == "SH1106"


# ---------------------------------------------------------------------------
# 第 2 步：入库归属 + 实体聚合检索
# ---------------------------------------------------------------------------


class EntityAwareExtractor:
    """产出两条参数原子：一条 scope=SH1106（芯片），一条 scope=1.3寸横屏（模组）。"""

    def extract(self, *, title, source_uri, batches):
        del title, source_uri
        from luxar.domain.knowledge_atoms import ParameterAtomDraft

        return KnowledgeExtraction(
            atoms=[],
            parameters=[
                ParameterAtomDraft(
                    parameter="command_display_on_off",
                    value_type="bytes",
                    value="0xAE 0xAF",
                    scope={"controller": "SH1106"},
                    source_pages=[12],
                    source_excerpt="0xAE 0xAF",
                ),
                ParameterAtomDraft(
                    parameter="init_sequence",
                    value_type="bytes",
                    value="0xAE 0xD5 0x80 0xA1 0xC8 0xAF",
                    scope={"device": "1.3寸横屏", "controller": "SH1106"},
                    source_pages=[12],
                    source_excerpt="0xAE 0xD5 0x80 0xA1 0xC8 0xAF",
                ),
            ],
        )


def test_ingest_assigns_entity_from_scope(tmp_path: Path) -> None:
    """参数原子入库时按 scope 匹配已注册实体，entity_id 写入原子。"""
    from luxar.domain.hardware_entities import HardwareEntity, entity_id_for

    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    chip = HardwareEntity(
        entity_id=entity_id_for("chip", "SH1106"),
        kind="chip",
        name="SH1106",
    )
    device = HardwareEntity(
        entity_id=entity_id_for("device", "1.3寸横屏"),
        kind="device",
        name="1.3寸横屏",
        chip_ref=chip.entity_id,
    )
    service.register_entity(entity=chip)
    service.register_entity(entity=device)

    service.ingest_pdf(
        project_key="0:oled",
        source_uri="docs/panel.pdf",
        title="屏厂规格书",
        path=tmp_path / "not-opened.pdf",
        reader=ParameterReader(),
        extractor=EntityAwareExtractor(),
    )
    # 检索显示 command 命令归属 chip 实体
    matches = service.search(project_key="0:oled", query="0xAE 0xAF 显示开关", limit=10)
    by_id = {m.subject: m for m in matches}
    assert by_id["command_display_on_off"].entity_id == chip.entity_id
    assert by_id["init_sequence"].entity_id == device.entity_id


def test_device_knowledge_aggregates_chip_chain(tmp_path: Path) -> None:
    """device_knowledge：返回 device + 其 chip 链上所有实体的原子（聚合）。"""
    from luxar.domain.hardware_entities import HardwareEntity, entity_id_for

    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    chip = HardwareEntity(
        entity_id=entity_id_for("chip", "SH1106"),
        kind="chip",
        name="SH1106",
    )
    device = HardwareEntity(
        entity_id=entity_id_for("device", "1.3寸横屏"),
        kind="device",
        name="1.3寸横屏",
        chip_ref=chip.entity_id,
    )
    service.register_entity(entity=chip)
    service.register_entity(entity=device)
    service.ingest_pdf(
        project_key="0:oled",
        source_uri="docs/panel.pdf",
        title="屏厂规格书",
        path=tmp_path / "not-opened.pdf",
        reader=ParameterReader(),
        extractor=EntityAwareExtractor(),
    )
    # 按 device 聚合：应拿到 device 自己的 init_sequence + chip 的 command
    knowledge = service.device_knowledge(device)
    subjects = {item.subject for item in knowledge}
    assert "init_sequence" in subjects  # device 自有
    assert "command_display_on_off" in subjects  # chip 沿引用带上
    assert len(knowledge) == 2


def test_reattach_after_late_registration_keeps_device_ownership(
    tmp_path: Path,
) -> None:
    """实体晚于文档注册：chip 先注册不得抢占 device 专属原子（注册顺序无关）。"""
    from luxar.domain.hardware_entities import HardwareEntity, entity_id_for

    service = KnowledgeService(
        LanceDBKnowledgeIndex(tmp_path / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )
    # 先入库（此时无实体 → 原子未归属）
    service.ingest_pdf(
        project_key="0:oled",
        source_uri="docs/panel.pdf",
        title="屏厂规格书",
        path=tmp_path / "not-opened.pdf",
        reader=ParameterReader(),
        extractor=EntityAwareExtractor(),
    )
    # 后注册：chip 先于 device
    chip = HardwareEntity(
        entity_id=entity_id_for("chip", "SH1106"),
        kind="chip",
        name="SH1106",
    )
    device = HardwareEntity(
        entity_id=entity_id_for("device", "1.3寸横屏"),
        kind="device",
        name="1.3寸横屏",
        chip_ref=chip.entity_id,
    )
    service.register_entity(entity=chip)
    service.register_entity(entity=device)
    # init_sequence 有 device+controller 双 scope：必须归属 device（chip 不抢）
    knowledge = service.device_knowledge(service.find_entity("1.3寸横屏"))
    by_subject = {item.subject: item for item in knowledge}
    assert by_subject["init_sequence"].entity_id == device.entity_id
    assert by_subject["command_display_on_off"].entity_id == chip.entity_id
