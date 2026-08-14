"""Web 项目目录边界：把浏览器项目名安全解析为受控 ESP-IDF 目录。"""

from __future__ import annotations

from pathlib import Path, PurePath

from luxar.web_contracts import WebProject


class WebProjectError(ValueError):
    """项目名或项目目录不满足 Web 边界；消息必须保持固定和安全。"""


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError as error:
        raise WebProjectError("项目路径无效") from error


class WebProjectCatalog:
    """只列出并解析一个显式根目录下的直接子项目。"""

    def __init__(self, projects_root: Path) -> None:
        if not projects_root.is_absolute():
            raise WebProjectError("项目根目录必须是绝对路径")
        if _is_link_or_junction(projects_root):
            raise WebProjectError("项目根目录不能是链接")
        try:
            self._root = projects_root.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise WebProjectError("项目根目录无效") from error
        if not self._root.is_dir():
            raise WebProjectError("项目根目录无效")

    def resolve(self, name: str) -> Path:
        """把一个不含路径语法的名称解析为现有 ESP-IDF 项目。"""

        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or ":" in name
            or PurePath(name).name != name
        ):
            raise WebProjectError("项目名称无效")

        candidate = self._root / name
        if _is_link_or_junction(candidate):
            raise WebProjectError("项目目录不能是链接")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._root)
        except (OSError, RuntimeError, ValueError) as error:
            raise WebProjectError("项目不存在或不在允许目录内") from error

        cmake_file = resolved / "CMakeLists.txt"
        if (
            not resolved.is_dir()
            or not cmake_file.is_file()
            or _is_link_or_junction(cmake_file)
        ):
            raise WebProjectError("目录不是有效的 ESP-IDF 项目")
        return resolved

    def list_projects(self) -> list[WebProject]:
        """跳过不安全/无效条目，只返回确定排序的安全描述。"""

        projects: list[WebProject] = []
        try:
            entries = sorted(self._root.iterdir(), key=lambda item: item.name)
        except OSError as error:
            raise WebProjectError("无法读取项目目录") from error

        for entry in entries:
            try:
                self.resolve(entry.name)
            except WebProjectError:
                continue
            projects.append(WebProject(name=entry.name))
        return projects
