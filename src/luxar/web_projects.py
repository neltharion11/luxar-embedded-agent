"""Web 项目目录边界：把浏览器项目名安全解析为受控 ESP-IDF 目录。"""

from __future__ import annotations

import re
from pathlib import Path, PurePath

from luxar.web_contracts import WebProject


_MAX_ROOT_CMAKE_BYTES = 64 * 1024
_MAX_TARGET_CONFIG_BYTES = 64 * 1024
_TARGET_LINE_PREFIX = "CONFIG_IDF_TARGET="


class WebProjectError(ValueError):
    """项目名或项目目录不满足 Web 边界；消息必须保持固定和安全。"""


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError as error:
        raise WebProjectError("项目路径无效") from error


def _is_espidf_root(cmake_file: Path) -> bool:
    """用 ESP-IDF 标准根 include 区分普通 CMake/STM32 项目。"""

    try:
        if cmake_file.stat().st_size > _MAX_ROOT_CMAKE_BYTES:
            return False
        content = cmake_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    normalized = content.replace("\\", "/")
    return (
        "IDF_PATH" in normalized
        and "tools/cmake/project.cmake" in normalized
    )


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

    @property
    def root(self) -> Path:
        """返回已经规范化并验证过的项目根目录。"""

        return self._root

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
            or not _is_espidf_root(cmake_file)
        ):
            raise WebProjectError("目录不是有效的 ESP-IDF 项目")
        return resolved

    def target_chip(self, name: str) -> str | None:
        """Read the immutable target written during create/select."""

        project = self.resolve(name)
        config = project / "sdkconfig.defaults"
        if not config.exists():
            return None
        if _is_link_or_junction(config):
            raise WebProjectError("项目目标配置无效")
        try:
            if config.stat().st_size > _MAX_TARGET_CONFIG_BYTES:
                raise WebProjectError("项目目标配置无效")
            content = config.read_text(encoding="utf-8", errors="strict")
        except WebProjectError:
            raise
        except (OSError, UnicodeError) as error:
            raise WebProjectError("项目目标配置无效") from error

        targets = [
            line.removeprefix(_TARGET_LINE_PREFIX).strip()
            for line in content.splitlines()
            if line.startswith(_TARGET_LINE_PREFIX)
        ]
        if not targets:
            return None
        if len(set(targets)) != 1 or not re.fullmatch(
            r"[a-z][a-z0-9_]*", targets[0]
        ):
            raise WebProjectError("项目目标配置无效")
        return targets[0]

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
            try:
                target_chip = self.target_chip(entry.name)
            except WebProjectError:
                continue
            projects.append(
                WebProject(name=entry.name, target_chip=target_chip)
            )
        return projects
