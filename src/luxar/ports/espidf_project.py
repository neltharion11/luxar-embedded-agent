"""项目创建 Port：规定“在受控父目录内创建 ESP-IDF 项目”能力的最小接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from luxar.domain.projects import ProjectEvidence


class EspIdfProjectPort(Protocol):
    # 只有具体 Adapter 才能运行 idf.py create-project；核心层只依赖这个签名。
    def create_project(
        self,
        parent_dir: Path,
        project_name: str,
        target_chip: str,
    ) -> ProjectEvidence:
        ...
