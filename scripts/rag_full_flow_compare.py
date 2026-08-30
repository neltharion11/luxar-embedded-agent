"""RAG 全流程对比（单 PDF）：全批次提取 + 双索引召回，新旧版本对照。

用法:
    python scripts/rag_full_flow_compare.py <pdf路径> [--output 报告.md]

与 rag_eval_compare.py 的区别：不截断批次（跑完整文档），提取后分别入库
旧/新两个索引，跑固定查询集输出对比表格。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile
import uuid
from pathlib import Path


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


def _document_id(source_uri: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"luxar:{source_uri}"))


def _ingest(service, *, source_uri, title, extraction) -> int:
    from luxar.domain.knowledge_atoms import (
        materialize_knowledge_atoms,
        materialize_parameter_atoms,
    )

    document_id = _document_id(source_uri)
    atoms = materialize_knowledge_atoms(
        extraction.atoms, document_id=document_id,
        source_uri=source_uri, source_title=title,
    )
    parameters = materialize_parameter_atoms(
        extraction.parameters, document_id=document_id,
        source_uri=source_uri, source_title=title,
    )
    all_atoms = atoms + parameters
    if not all_atoms:
        return 0
    service.ingest_atoms(
        project_key="eval", source_uri=source_uri, title=title,
        atoms=all_atoms,
        content_hash=hashlib.sha256(source_uri.encode()).hexdigest(),
    )
    return len(all_atoms)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="RAG 全流程新旧对比（单 PDF）")
    parser.add_argument("pdf_path")
    parser.add_argument("--output", default="docs/rag-full-flow-compare.md")
    args = parser.parse_args(argv)

    _load_dotenv()
    sys.stdout.reconfigure(encoding="utf-8")

    from luxar.adapters.deepseek.client import OpenAICompatibleJsonClient
    from luxar.adapters.deepseek.knowledge_extractor import DeepSeekKnowledgeAtomExtractor
    from luxar.adapters.deepseek.settings import DeepSeekSettings
    from luxar.document_reader import PdfDocumentReader, configured_drawing_analyzer, iter_pdf_batches
    from luxar.knowledge import KnowledgeService, LocalHashEmbeddingAdapter
    from luxar.lance_knowledge import LanceDBKnowledgeIndex

    settings = DeepSeekSettings()
    model = settings.fast_model
    client = OpenAICompatibleJsonClient(settings)
    legacy_extractor = DeepSeekKnowledgeAtomExtractor(client, model, include_parameters=False)
    new_extractor = DeepSeekKnowledgeAtomExtractor(client, model)

    pdf = Path(args.pdf_path)
    title = pdf.stem
    uri = pdf.as_uri()
    reader = PdfDocumentReader(drawing_analyzer=configured_drawing_analyzer())
    batches = list(iter_pdf_batches(reader, pdf))
    print(f"文档: {title}，{len(batches)} 个批次（全量，不截断）", flush=True)
    for index, batch in enumerate(batches, 1):
        print(f"  批 {index}: 页 {batch.start_page}-{batch.end_page} "
              f"字符 {len(batch.content)} 章节={batch.section_title[:40]}", flush=True)

    with tempfile.TemporaryDirectory(prefix="rag-full-") as tmp:
        tmp_path = Path(tmp)
        legacy_index = KnowledgeService(
            LanceDBKnowledgeIndex(tmp_path / "legacy.lance", dimensions=64),
            LocalHashEmbeddingAdapter(64),
        )
        new_index = KnowledgeService(
            LanceDBKnowledgeIndex(tmp_path / "new.lance", dimensions=64),
            LocalHashEmbeddingAdapter(64),
        )

        print("旧版提取（全批次）...", flush=True)
        legacy_result = legacy_extractor.extract(title=title, source_uri=uri, batches=batches)
        legacy_count = _ingest(legacy_index, source_uri=uri, title=title, extraction=legacy_result)
        print(f"  旧版入库 {legacy_count} 条（纯散文原子）", flush=True)

        print("新版提取（全批次）...", flush=True)
        new_result = new_extractor.extract(title=title, source_uri=uri, batches=batches)
        new_count = _ingest(new_index, source_uri=uri, title=title, extraction=new_result)
        print(f"  新版入库 {new_count} 条（散文 {len(new_result.atoms)} + 参数 {len(new_result.parameters)}）", flush=True)

        queries = [
            "SH1106 初始化序列 init sequence",
            "SH1106 显示开关命令 0xAE 0xAF",
            "SH1106 接口模式引脚 IM0 IM1 IM2",
            "SH1106 列偏移 column offset",
            "SH1106 时钟分频 0xD5",
            "0xD5 0x80",
            "SH1106 COM 扫描方向 0xC8",
            "SH1106 页寻址模式 page addressing",
            "SH1106 预充电周期 precharge 0xD9",
            "SH1106 VCOM deselect 0xDB",
        ]

        rows = []
        for query in queries:
            legacy_hits = legacy_index.search(project_key="eval", query=query, limit=3)
            new_hits = new_index.search(project_key="eval", query=query, limit=3)
            rows.append({
                "query": query,
                "legacy_top": f"{legacy_hits[0].category}:{legacy_hits[0].subject[:46]}" if legacy_hits else "无命中",
                "new_top": f"{new_hits[0].category}:{new_hits[0].subject[:46]}" if new_hits else "无命中",
                "new_param_hits": sum(1 for h in new_hits if h.category == "parameter"),
                "legacy_score": round(legacy_hits[0].score, 3) if legacy_hits else None,
                "new_score": round(new_hits[0].score, 3) if new_hits else None,
            })

        # 新入库参数原子清单（供报告佐证）
        param_names = sorted({p.parameter for p in new_result.parameters})
        lines = [
            f"# RAG 全流程对比报告：{title}",
            "",
            f"- 批次：{len(batches)} 个（**全量**，未截断）",
            f"- 旧版入库：{legacy_count} 条散文原子；新版入库：{new_count} 条"
            f"（散文 {len(new_result.atoms)} + 参数 {len(new_result.parameters)}）",
            f"- 新版参数原子清单：{', '.join(param_names[:40])}" + ("..." if len(param_names) > 40 else ""),
            "",
            "| 查询 | 旧版top1 | 新版top1 | 新版参数命中数 | 旧分数 | 新分数 |",
            "|---|---|---|---|---|---|",
        ]
        for row in rows:
            lines.append(
                "| {query} | {legacy_top} | {new_top} | {new_param_hits} "
                "| {legacy_score} | {new_score} |".format(**row)
            )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n报告已写入: {output}", flush=True)
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
