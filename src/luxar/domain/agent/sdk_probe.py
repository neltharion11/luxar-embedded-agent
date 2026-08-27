"""只读 SDK 头文件探测：把"缺失头文件"的构建诊断接地到已安装 ESP-IDF。

这里只放纯领域逻辑：从 BuildEvidence 里提取缺失的头文件名，以及一次探测的
结构化结果。真正扫描已安装 SDK 树的能力属于 Adapter（:mod:`luxar.adapters.espidf_sdk_probe`）。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from luxar.domain.evidence import BuildEvidence


_MISSING_INCLUDE_RE = re.compile(
    r"fatal error\s*:\s*['\"]?([A-Za-z0-9_./-]+\.h)"
)


class SdkIncludeResolution(BaseModel):
    """一次头文件探测的只读结果。"""

    include_name: str = Field(min_length=1)
    exists: bool
    candidates: list[str] = Field(default_factory=list, max_length=16)
    searched_root: str | None = None


def missing_include_names(evidence: BuildEvidence) -> list[str]:
    """从构建诊断中提取"找不到头文件"的头文件名（去重、保序、有界）。"""

    names: list[str] = []
    for diagnostic in evidence.diagnostics:
        match = _MISSING_INCLUDE_RE.search(diagnostic.message)
        if match is None:
            continue
        header = match.group(1).strip().replace("\\", "/").lstrip("./")
        if header and header not in names:
            names.append(header)
        if len(names) >= 8:
            break
    return names


_SYMBOL_PATTERNS = (
    re.compile(
        r"implicit declaration of function\s*[`'\"]([A-Za-z_][A-Za-z0-9_]*)[`'\"]"
    ),
    re.compile(r"unknown type name\s*[`'\"]([A-Za-z_][A-Za-z0-9_]*)[`'\"]"),
    re.compile(r"no member named\s*[`'\"]([A-Za-z_][A-Za-z0-9_]*)[`'\"]"),
    re.compile(r"['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s+is deprecated"),
)


class SdkMigrationSnippet(BaseModel):
    """迁移指南里一处与某符号相关的只读摘录。"""

    guide: str = Field(min_length=1)
    snippet: str = Field(min_length=1)


def changed_api_names(evidence: BuildEvidence) -> list[str]:
    """从构建诊断提取"API 改名/移除/弃用"信号里的符号名（去重、保序、有界）。"""

    names: list[str] = []
    for diagnostic in evidence.diagnostics:
        for pattern in _SYMBOL_PATTERNS:
            for match in pattern.finditer(diagnostic.message):
                name = match.group(1)
                if name and name not in names:
                    names.append(name)
                if len(names) >= 8:
                    return names
    return names


__all__ = [
    "SdkIncludeResolution",
    "SdkMigrationSnippet",
    "changed_api_names",
    "missing_include_names",
]
