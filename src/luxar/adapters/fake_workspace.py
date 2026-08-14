"""工作区 Fake：模拟读取和应用修复，记录调用但不会真正访问或修改磁盘。"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from luxar.domain.repairs import ProjectFile, RepairPlan


class FakeWorkspace:
    def __init__(
        self,
        files: Sequence[ProjectFile],
    ) -> None:
        # Sequence 允许 list/tuple 等输入；内部复制后由 Fake 自己管理。
        self.files = list(files)
        self.read_calls: list[Path] = []
        self.apply_calls: list[tuple[Path, RepairPlan]] = []

    def read_project_files(
        self,
        project_path: Path,
    ) -> list[ProjectFile]:
        self.read_calls.append(project_path)

        # 返回副本，防止调用方修改 Fake 内部保存的测试文件。
        return list(self.files)

    def apply_repair(
        self,
        project_path: Path,
        repair: RepairPlan,
    ) -> list[str]:
        self.apply_calls.append((project_path, repair))

        # Fake 只报告计划里的目标路径，不产生真实文件副作用。
        return [
            replacement.path
            for replacement in repair.replacements
        ]
