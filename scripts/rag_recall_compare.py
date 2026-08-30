"""RAG 召回环节端到端对比：旧版索引（散文原子）vs 新版索引（+参数原子+hex词法加权）。

流程：对每份 PDF 用新旧提取器各跑一轮 → 分别入库两个临时 LanceDB 索引
（同一本地哈希 embedding）→ 固定查询集在两个索引上各查 top-k → 对比命中质量。

指标：
- 参数型查询命中参数原子的比例（新版 > 0，旧版恒 0）；
- hex 精确查询（如 "0xD5 0x80"）的命中（新版词法加权 vs 旧版）；
- top1 命中类别/主题。

用法:
    python scripts/rag_recall_compare.py <pdf目录> [--max-batches 2] [--max-windows 4]
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

_QUERIES: dict[str, list[str]] = {
    "1.3寸竖屏横屏-控制芯片SH1106手册": [
        "SH1106 初始化序列 init sequence",
        "SH1106 显示开关命令 0xAE 0xAF",
        "SH1106 接口模式引脚 IM0 IM1 IM2",
        "SH1106 列偏移 column offset",
        "0xD5 0x80",
    ],
    "CH340": [
        "CH340 波特率 baud rate",
        "CH340 引脚映射 TXD RXD",
        "CH340 供电电压 VCC",
    ],
    "DHT11": [
        "DHT11 数据格式 40 位",
        "DHT11 起始信号时序",
        "DHT11 温度测量范围",
    ],
    "STM32F103ZET6（中文版）": [
        "STM32 时钟频率 72MHz",
        "STM32 GPIO 输入输出模式",
    ],
}


def _load_dotenv() -> None:
    candidates = [Path(__file__).resolve().parents[1] / ".env"]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


def _build_index(directory: Path):
    """建临时 LanceDB 索引 + KnowledgeService（本地哈希 embedding）。"""
    from luxar.knowledge import KnowledgeService, LocalHashEmbeddingAdapter
    from luxar.lance_knowledge import LanceDBKnowledgeIndex

    return KnowledgeService(
        LanceDBKnowledgeIndex(directory / "knowledge.lance", dimensions=64),
        LocalHashEmbeddingAdapter(64),
    )


def _ingest_extraction(service, *, project_key, source_uri, title, extraction):
    """把一次提取产物（atoms+parameters）入库，复用 KnowledgeService 逻辑。"""
    from luxar.domain.knowledge_atoms import (
        materialize_knowledge_atoms,
        materialize_parameter_atoms,
    )

    document_id = _document_id_for(source_uri)
    atoms = materialize_knowledge_atoms(
        extraction.atoms,
        document_id=document_id,
        source_uri=source_uri,
        source_title=title,
    )
    parameter_atoms = materialize_parameter_atoms(
        extraction.parameters,
        document_id=document_id,
        source_uri=source_uri,
        source_title=title,
    )
    all_atoms = atoms + parameter_atoms
    if not all_atoms:
        return 0
    import hashlib

    content_hash = hashlib.sha256(source_uri.encode("utf-8")).hexdigest()
    service.ingest_atoms(
        project_key=project_key,
        source_uri=source_uri,
        title=title,
        atoms=all_atoms,
        content_hash=content_hash,
    )
    return len(all_atoms)


def _document_id_for(source_uri: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"luxar:{source_uri}"))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="RAG 召回环节端到端对比")
    parser.add_argument("pdf_dir")
    parser.add_argument("--max-batches", type=int, default=2)
    parser.add_argument("--max-windows", type=int, default=4)
    parser.add_argument("--output", default="docs/rag-recall-compare.md")
    args = parser.parse_args(argv)

    _load_dotenv()
    sys.stdout.reconfigure(encoding="utf-8")

    from luxar.adapters.deepseek.client import OpenAICompatibleJsonClient
    from luxar.adapters.deepseek.knowledge_extractor import DeepSeekKnowledgeAtomExtractor
    from luxar.adapters.deepseek.settings import DeepSeekSettings
    from luxar.document_reader import PdfDocumentReader, configured_drawing_analyzer, iter_pdf_batches

    settings = DeepSeekSettings()
    model = settings.fast_model
    client = OpenAICompatibleJsonClient(settings)
    legacy_extractor = DeepSeekKnowledgeAtomExtractor(
        client, model, include_parameters=False
    )
    new_extractor = DeepSeekKnowledgeAtomExtractor(client, model)

    with tempfile.TemporaryDirectory(prefix="rag-recall-") as tmp:
        tmp_path = Path(tmp)
        legacy_index = _build_index(tmp_path / "legacy")
        new_index = _build_index(tmp_path / "new")

        reader = PdfDocumentReader(drawing_analyzer=configured_drawing_analyzer())
        pdfs = sorted(set(Path(args.pdf_dir).glob("*.pdf")) | set(Path(args.pdf_dir).glob("*.PDF")))
        for pdf in pdfs:
            title = pdf.stem
            if title not in _QUERIES:
                continue
            uri = pdf.as_uri()
            print(f"提取: {title}", flush=True)
            batches = list(iter_pdf_batches(reader, pdf))[: args.max_batches]
            truncated = [
                replace(b, content=b.content[: args.max_windows * 18_000])
                if len(b.content) > args.max_windows * 18_000
                else b
                for b in batches
            ]
            # 旧版入库
            legacy_result = legacy_extractor.extract(
                title=title, source_uri=uri, batches=truncated
            )
            legacy_count = _ingest_extraction(
                legacy_index, project_key="eval", source_uri=uri,
                title=title, extraction=legacy_result,
            )
            # 新版入库
            new_result = new_extractor.extract(
                title=title, source_uri=uri, batches=truncated
            )
            new_count = _ingest_extraction(
                new_index, project_key="eval", source_uri=uri,
                title=title, extraction=new_result,
            )
            print(f"  入库: 旧版 {legacy_count} / 新版 {new_count}", flush=True)

        # ---- 查询对比 ----
        rows = []
        for doc, queries in _QUERIES.items():
            for query in queries:
                legacy_hits = legacy_index.search(
                    project_key="eval", query=query, limit=3
                )
                new_hits = new_index.search(
                    project_key="eval", query=query, limit=3
                )
                legacy_top = legacy_hits[0] if legacy_hits else None
                new_top = new_hits[0] if new_hits else None
                new_param_hits = sum(
                    1 for h in new_hits if h.category == "parameter"
                )
                rows.append(
                    {
                        "doc": doc,
                        "query": query,
                        "legacy_top": (
                            f"{legacy_top.category}:{legacy_top.subject[:40]}"
                            if legacy_top else "无命中"
                        ),
                        "new_top": (
                            f"{new_top.category}:{new_top.subject[:40]}"
                            if new_top else "无命中"
                        ),
                        "new_param_hits": new_param_hits,
                        "legacy_score": round(legacy_top.score, 3) if legacy_top else None,
                        "new_score": round(new_top.score, 3) if new_top else None,
                    }
                )

        # ---- 报告 ----
        lines = [
            "# RAG 召回环节端到端对比报告",
            "",
            f"查询集 {len(rows)} 条；top-3 命中统计。旧版索引=纯散文原子；"
            "新版索引=散文+参数原子+hex词法加权。",
            "",
            "| 文档 | 查询 | 旧版top1 | 新版top1 | 新版参数命中数 | 旧分数 | 新分数 |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in rows:
            lines.append(
                "| {doc} | {query} | {legacy_top} | {new_top} | {new_param_hits} "
                "| {legacy_score} | {new_score} |".format(**row)
            )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n报告已写入: {output}")
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
