"""ESP-IDF Fake：按预设顺序返回构建证据，用于稳定模拟失败、重试和成功。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from luxar.domain.evidence import BuildEvidence


class FakeEspIdf:
    def __init__(
        self,
        evidence_sequence: Sequence[BuildEvidence],
    ) -> None:
        # 复制为内部队列，避免调用方后来修改原 Sequence 干扰测试。
        self._remaining_evidence = list(evidence_sequence)
        self.calls: list[Path] = []

    def build(self, project_path: Path) -> BuildEvidence:
        self.calls.append(project_path)

        # 证据耗尽代表测试配置错误；Fake 不能擅自编造一次构建结果。
        if not self._remaining_evidence:
            raise RuntimeError(
                "FakeEspIdf has no configured build evidence remaining"
            )

        # pop(0) 依次取出证据，可模拟“第一次失败、第二次成功”的时间顺序。
        return self._remaining_evidence.pop(0)
