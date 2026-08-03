"""ESP-IDF Port：规定构建项目并返回真实 BuildEvidence 的工具能力。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from luxar.domain.evidence import BuildEvidence


class EspIdfPort(Protocol):
    # 只有具体 Adapter 才能运行 idf.py；核心层只依赖这个稳定签名。
    def build(self, project_path: Path) -> BuildEvidence:
        ...
