"""RAG 提取环节 A/B 对比评估：旧版（散文原子）vs 新版（+参数型原子+逐字闸门）。

用法:
    python scripts/rag_eval_compare.py <pdf目录> [--max-batches 2] [--max-windows 4]

对每份 PDF 取前 N 个章节批次（每批截断到 max_windows 个 18K 窗口），分别用
旧版提取器（include_parameters=False）与新版提取器（include_parameters=True）
各跑一轮，统计：
- 参数覆盖数：旧版恒 0，新版 >0 即新能力生效；
- 闸门丢弃：模型产出参数 vs 逐字校验不通过被丢弃 vs 最终入库；
- excerpt 逐字率：旧版事后子串检查（无闸门，模型自觉）vs 新版闸门后（恒 100%）；
- bytes 字节可定位率（仅新版）。

结果输出 markdown 报告 + JSON 数据。模型走 DEEPSEEK_*（默认 flash）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

_WHITESPACE_RE = re.compile(r"\s+")
_HEX_BYTE_RE = re.compile(r"0[xX][0-9A-Fa-f]{2}")
_SUFFIX_HEX_BYTE_RE = re.compile(r"(?<![0-9A-Fa-fxX])[0-9A-Fa-f]{2}[hH](?![0-9A-Fa-f])")
_WINDOW_CHARS = 18_000


def _normalize_bytes_text(text: str) -> set[str]:
    """与提取器闸门同语义：0xXX 与 AEH 两种记法都归一化为字节集合。"""
    result: set[str] = set()
    for token in _HEX_BYTE_RE.findall(text):
        result.add(token.upper())
    for token in _SUFFIX_HEX_BYTE_RE.findall(text):
        result.add("0X" + token[:-1].upper())
    return result


def _load_dotenv() -> None:
    """从仓库 .env 加载缺失的环境变量（与 cli 相同逻辑）。"""
    import os

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


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _verbatim_ok(excerpt: str, window: str) -> bool:
    norm = _normalize(excerpt)
    return bool(norm) and norm in _normalize(window)


def _bytes_located(value: str, excerpt: str) -> bool:
    value_bytes = _normalize_bytes_text(value)
    if not value_bytes:
        return True  # 无字节 token 无法机械校验，视为通过
    excerpt_bytes = _normalize_bytes_text(excerpt)
    return value_bytes.issubset(excerpt_bytes)


def _run_with_retry(fn, retries: int = 2):
    """模型输出不稳定（超时/超长/无效 JSON），失败自动重试一次。"""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fn(), None
        except Exception as error:
            last_error = error
            print(f"    (第 {attempt + 1} 次失败: {type(error).__name__}，重试)", flush=True)
    return None, last_error


def evaluate_one_pdf(
    *,
    legacy_extractor,
    new_extractor,
    batches: list,
    max_batches: int,
    max_windows: int,
    pdf_path: Path,
    retries: int = 2,
) -> dict[str, object]:
    """对一份 PDF 跑新旧两轮提取，返回对比指标（单轮失败不中断整体）。"""
    selected = batches[:max_batches]
    truncated = []
    for batch in selected:
        chars = max_windows * _WINDOW_CHARS
        if len(batch.content) > chars:
            truncated.append(replace(batch, content=batch.content[:chars]))
        else:
            truncated.append(batch)

    title = pdf_path.stem
    uri = pdf_path.as_uri()
    window0 = truncated[0].content if truncated else ""

    row: dict[str, object] = {
        "doc": title,
        "batches": len(truncated),
        "old_atom_count": None,
        "old_verbatim_ratio": None,
        "old_error": "",
        "new_atom_count": None,
        "new_atom_verbatim_ratio": None,
        "new_parameter_produced": None,
        "new_parameter_dropped": None,
        "new_parameter_kept": None,
        "new_bytes_params": None,
        "new_bytes_located_ratio": None,
        "new_error": "",
    }

    # ---- 旧版（无参数原子、无闸门）----
    legacy_result, legacy_error = _run_with_retry(
        lambda: legacy_extractor.extract(
            title=title, source_uri=uri, batches=truncated
        ),
        retries=retries,
    )
    if legacy_result is None:
        row["old_error"] = f"{type(legacy_error).__name__}: {str(legacy_error)[:120]}"
    else:
        old_atoms = legacy_result.atoms
        old_verbatim = sum(
            1
            for a in old_atoms
            if _verbatim_ok(a.source_excerpt or "", window0)
        )
        row["old_atom_count"] = len(old_atoms)
        row["old_verbatim_ratio"] = (
            round(old_verbatim / len(old_atoms), 3) if old_atoms else None
        )

    # ---- 新版（参数原子 + 闸门）----
    import luxar.adapters.deepseek.knowledge_extractor as ke

    produced: list[object] = []
    original_check = ke._verbatim_check

    def counting_check(parameter, window_content):
        ok = original_check(parameter, window_content)
        if not ok:
            produced.append(parameter)
        return ok

    ke._verbatim_check = counting_check
    new_result = None
    new_error: Exception | None = None
    try:
        new_result, new_error = _run_with_retry(
            lambda: new_extractor.extract(
                title=title, source_uri=uri, batches=truncated
            ),
            retries=retries,
        )
    finally:
        ke._verbatim_check = original_check
    if new_result is None:
        row["new_error"] = f"{type(new_error).__name__}: {str(new_error)[:120]}"

    if new_result is not None:
        new_parameters = new_result.parameters
        bytes_params = [p for p in new_parameters if p.value_type == "bytes"]
        bytes_located = sum(
            1 for p in bytes_params if _bytes_located(p.value, p.source_excerpt)
        )
        new_atom_verbatim = sum(
            1
            for a in new_result.atoms
            if _verbatim_ok(a.source_excerpt or "", window0)
        )
        row["new_atom_count"] = len(new_result.atoms)
        row["new_atom_verbatim_ratio"] = (
            round(new_atom_verbatim / len(new_result.atoms), 3)
            if new_result.atoms
            else None
        )
        row["new_parameter_produced"] = len(new_parameters) + len(produced)
        row["new_parameter_dropped"] = len(produced)
        row["new_parameter_kept"] = len(new_parameters)
        row["new_bytes_params"] = len(bytes_params)
        row["new_bytes_located_ratio"] = (
            round(bytes_located / len(bytes_params), 3) if bytes_params else None
        )
    return row


def _render_markdown(rows: list[dict[str, object]]) -> str:
    lines = [
        "# RAG 提取环节 A/B 对比报告",
        "",
        "旧版：散文型知识原子（无参数原子、excerpt 靠模型自觉）。",
        "新版：+参数型原子（面向代码生成的结构化值）+ 逐字子串机械闸门（不通过即丢弃）。",
        "",
        "| 文档 | 批次 | 旧原子数 | 旧excerpt逐字率 | 新原子数 | 新参数(产出) | 新参数(丢弃) | 新参数(入库) | 新bytes可定位率 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {doc} | {batches} | {old_atom_count} | {old_verbatim_ratio} "
            "| {new_atom_count} | {new_parameter_produced} | {new_parameter_dropped} "
            "| {new_parameter_kept} | {new_bytes_located_ratio} |".format(**row)
        )
    lines.append("")
    lines.append("## 单轮失败明细")
    lines.append("")
    for row in rows:
        if row.get("old_error") or row.get("new_error"):
            lines.append(f"- **{row['doc']}**")
            if row.get("old_error"):
                lines.append(f"  - 旧版: {row['old_error']}")
            if row.get("new_error"):
                lines.append(f"  - 新版: {row['new_error']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="RAG 提取环节 A/B 对比评估")
    parser.add_argument("pdf_dir", help="数据手册 PDF 目录")
    parser.add_argument("--max-batches", type=int, default=2)
    parser.add_argument("--max-windows", type=int, default=4)
    parser.add_argument("--model", default=None, help="覆盖模型（默认 DEEPSEEK_FAST_MODEL）")
    parser.add_argument("--output", default=None, help="报告输出路径（markdown）")
    args = parser.parse_args(argv)

    _load_dotenv()
    sys.stdout.reconfigure(encoding="utf-8")

    from luxar.adapters.deepseek.client import OpenAICompatibleJsonClient
    from luxar.adapters.deepseek.knowledge_extractor import DeepSeekKnowledgeAtomExtractor
    from luxar.adapters.deepseek.settings import DeepSeekSettings
    from luxar.document_reader import PdfDocumentReader, configured_drawing_analyzer

    settings = DeepSeekSettings()
    model = args.model or settings.fast_model
    client = OpenAICompatibleJsonClient(settings)
    legacy_extractor = DeepSeekKnowledgeAtomExtractor(
        client, model, include_parameters=False
    )
    new_extractor = DeepSeekKnowledgeAtomExtractor(client, model)

    pdf_dir = Path(args.pdf_dir)
    pdfs = sorted(set(pdf_dir.glob("*.pdf")) | set(pdf_dir.glob("*.PDF")))
    if not pdfs:
        print("目录中没有 PDF", file=sys.stderr)
        return 2

    reader = PdfDocumentReader(drawing_analyzer=configured_drawing_analyzer())
    rows: list[dict[str, object]] = []
    for index, pdf in enumerate(pdfs, start=1):
        print(f"[{index}/{len(pdfs)}] 读取批次: {pdf.name}", flush=True)
        try:
            batches = list(__import__("luxar.document_reader", fromlist=["iter_pdf_batches"]).iter_pdf_batches(reader, pdf))
        except Exception as error:
            print(f"  跳过（读取失败）: {error}", flush=True)
            continue
        print(f"  批次 {len(batches)} 个，开始新旧对比（{args.max_batches} 批 × {args.max_windows} 窗口）", flush=True)
        row = evaluate_one_pdf(
            legacy_extractor=legacy_extractor,
            new_extractor=new_extractor,
            batches=batches,
            max_batches=args.max_batches,
            max_windows=args.max_windows,
            pdf_path=pdf,
        )
        rows.append(row)
        print(
            f"  旧原子={row['old_atom_count']}(逐字率 {row['old_verbatim_ratio']}) "
            f"新参数=产出 {row['new_parameter_produced']} / 丢弃 {row['new_parameter_dropped']} "
            f"/ 入库 {row['new_parameter_kept']}", flush=True
        )

    markdown = _render_markdown(rows)
    output = Path(args.output) if args.output else Path("docs") / "rag-extraction-compare.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    print(f"\n报告已写入: {output}")
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
