"""项目创建 Fake：按预设顺序返回创建证据，不产生任何磁盘副作用。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from luxar.domain.projects import ProjectEvidence


class FakeProjectCreator:
    def __init__(
        self,
        evidence_sequence: Sequence[ProjectEvidence],
    ) -> None:
        # 复制为内部队列，避免调用方后来修改原 Sequence 干扰测试。
        self._remaining_evidence = list(evidence_sequence)
        self.calls: list[tuple[Path, str, str]] = []

    def create_project(
        self,
        parent_dir: Path,
        project_name: str,
        target_chip: str,
    ) -> ProjectEvidence:
        self.calls.append((parent_dir, project_name, target_chip))

        # 证据耗尽代表测试配置错误；Fake 不能擅自编造一次创建结果。
        if not self._remaining_evidence:
            raise RuntimeError(
                "FakeProjectCreator has no configured evidence remaining"
            )

        return self._remaining_evidence.pop(0)
