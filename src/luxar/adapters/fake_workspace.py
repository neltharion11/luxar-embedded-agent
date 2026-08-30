"""工作区 Fake：模拟读取和应用修复，记录调用但不会真正访问或修改磁盘。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence

from luxar.domain.repairs import ProjectFile, RepairPlan


def _content_hash(content: str) -> str:
    """与真实 LocalWorkspace 的语义对齐：对文件内容做 SHA-256（小写 hex）。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class FakeWorkspace:
    project_exists = True

    def __init__(
        self,
        files: Sequence[ProjectFile],
    ) -> None:
        # Sequence 允许 list/tuple 等输入；内部复制后由 Fake 自己管理。
        self.files = [
            ProjectFile(
                path=item.path,
                content=item.content,
                sha256=item.sha256 or _content_hash(item.content),
            )
            for item in files
        ]
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

        replacements = {
            replacement.path: replacement.content
            for replacement in repair.replacements
        }
        existing_paths = {item.path for item in self.files}
        self.files = [
            ProjectFile(
                path=item.path,
                content=replacements.get(item.path, item.content),
                sha256=_content_hash(replacements.get(item.path, item.content)),
            )
            for item in self.files
        ]
        self.files.extend(
            ProjectFile(path=path, content=content, sha256=_content_hash(content))
            for path, content in replacements.items()
            if path not in existing_paths
        )

        # 在内存中反映写入，以便刷新项目指纹的测试符合真实 Workspace 语义。
        return [
            replacement.path
            for replacement in repair.replacements
        ]
