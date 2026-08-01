from __future__ import annotations

from pathlib import Path
from typing import Protocol

from luxar.domain.evidence import BuildEvidence


class EspIdfPort(Protocol):
    def build(self, project_path: Path) -> BuildEvidence:
        ...