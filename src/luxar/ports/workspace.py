"""工作区 Port：规定受控读取项目文件和安全应用修复计划的能力。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from luxar.domain.repairs import ProjectFile, RepairPlan


class WorkspacePort(Protocol):
    # 读取返回经过验证的 ProjectFile，而不是把任意文件句柄交给 LLM。
    def read_project_files(
        self,
        project_path: Path,
    ) -> list[ProjectFile]:
        ...

    def apply_repair(
        self,
        project_path: Path,
        repair: RepairPlan,
    ) -> list[str]:
        # 返回真正被应用的项目相对路径，供 State 记录本次副作用。
        ...
