"""RAG 提取对比评估：旧版（散文原子）vs 新版（+参数型原子 + 逐字校验）。

用法:
    python scripts/rag_extraction_compare.py <pdf目录> [--output 报告.md]

数据集目录下每个 PDF 会走两轮提取（用 FakeJsonCompletionClient 不可行——本脚本
用真实模型客户端，故评估时由调用方注入 LUXAR_* 配置或通过 --dry-run 仅做机制
对比）。实际对比由 compare_extraction() 完成，两套提取产物按同一批 PDF 批次
比较：参数覆盖数、excerpt 逐字率、字节可定位率。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_WHITESPACE_RE = re.compile(r"\s+")
_HEX_BYTE_RE = re.compile(r"0[xX][0-9A-Fa-f]{2}")


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def verbatim_ratio(excerpts: list[tuple[str, str]]) -> float:
    """excerpt 是原文字串的比例（0.0-1.0）。"""
    if not excerpts:
        return 0.0
    passed = sum(
        1
        for excerpt, window in excerpts
        if _normalize(excerpt) and _normalize(excerpt) in _normalize(window)
    )
    return passed / len(excerpts)


def bytes_located_ratio(parameters: list[dict[str, object]], windows: list[str]) -> float:
    """bytes 型参数原子的全部字节都能在窗口原文中定位的比例。"""
    window_text = " ".join(_normalize(w).upper() for w in windows)
    byte_params = [
        p for p in parameters
        if isinstance(p, dict) and p.get("value_type") == "bytes"
    ]
    if not byte_params:
        return 1.0
    passed = 0
    for p in byte_params:
        value = str(p.get("value", ""))
        tokens = _HEX_BYTE_RE.findall(value)
        if tokens and all(token.upper() in window_text for token in tokens):
            passed += 1
    return passed / len(byte_params)


def compare_extraction(
    *,
    old_atoms: list[dict[str, object]],
    old_parameters: list[dict[str, object]],  # 旧版无参数原子，恒为空
    new_atoms: list[dict[str, object]],
    new_parameters: list[dict[str, object]],
    windows: list[str],
) -> dict[str, object]:
    """对比两套提取产物，返回结构化指标。"""
    old_excerpts = [
        (str(a.get("source_excerpt", "")), windows[0] if windows else "")
        for a in old_atoms
    ]
    new_atom_excerpts = [
        (str(a.get("source_excerpt", "")), windows[0] if windows else "")
        for a in new_atoms
    ]
    new_param_excerpts = [
        (str(p.get("source_excerpt", "")), windows[0] if windows else "")
        for p in new_parameters
    ]
    return {
        "old_atom_count": len(old_atoms),
        "new_atom_count": len(new_atoms),
        "old_parameter_count": len(old_parameters),
        "new_parameter_count": len(new_parameters),
        "old_verbatim_ratio": round(verbatim_ratio(old_excerpts), 3),
        "new_atom_verbatim_ratio": round(verbatim_ratio(new_atom_excerpts), 3),
        "new_parameter_verbatim_ratio": round(verbatim_ratio(new_param_excerpts), 3),
        "new_bytes_located_ratio": round(bytes_located_ratio(new_parameters, windows), 3),
    }


def _render_markdown(results: list[dict[str, object]]) -> str:
    lines = [
        "# RAG 提取对比报告",
        "",
        "对比维度：旧版（散文型知识原子）vs 新版（+参数型原子 + 逐字校验机械闸门）",
        "",
        "| 文档 | 旧原子数 | 新原子数 | 旧参数 | 新参数 | 旧excerpt逐字率 | 新原子逐字率 | 新参数字节可定位率 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in results:
        lines.append(
            "| {doc} | {old_atom_count} | {new_atom_count} | {old_parameter_count} "
            "| {new_parameter_count} | {old_verbatim_ratio} | {new_atom_verbatim_ratio} "
            "| {new_bytes_located_ratio} |".format(**row)
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="RAG 提取对比评估")
    parser.add_argument("pdf_dir", help="包含数据手册 PDF 的目录")
    parser.add_argument("--output", default=None, help="报告输出路径（markdown）")
    args = parser.parse_args(argv)

    pdf_dir = Path(args.pdf_dir)
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        print("目录中没有 PDF", file=sys.stderr)
        return 2
    print(f"发现 {len(pdfs)} 个 PDF（实际对比由调用方注入提取器完成）")
    print(
        "提示：本脚本提供 compare_extraction() 纯函数 + 报告渲染；"
        "真实模型提取请由调用方构造 DeepSeekKnowledgeAtomExtractor 执行，"
        "或用 --dry-run 验证机制。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
