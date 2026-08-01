from __future__ import annotations

from pathlib import Path
from typing import Sequence

from luxar.domain.evidence import BuildEvidence


class FakeEspIdf:
    def __init__(
        self,
        evidence_sequence: Sequence[BuildEvidence],
    ) -> None:
        self._remaining_evidence = list(evidence_sequence)
        self.calls: list[Path] = []

    def build(self, project_path: Path) -> BuildEvidence:
        self.calls.append(project_path)

        if not self._remaining_evidence:
            raise RuntimeError(
                "FakeEspIdf has no configured build evidence remaining"
            )

        return self._remaining_evidence.pop(0)