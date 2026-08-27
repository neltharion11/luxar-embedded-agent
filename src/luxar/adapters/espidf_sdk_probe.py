"""只读 ESP-IDF 头文件探测：检查存在性并给出最接近的替代头文件。"""

from __future__ import annotations

from pathlib import Path

from luxar.domain.agent.sdk_probe import (
    SdkIncludeResolution,
    SdkMigrationSnippet,
)


_MAX_CANDIDATES = 8
_MIGRATION_GUIDES_DIR = "docs/en/migration-guides"
_MIGRATION_EXTS = (".rst", ".md", ".txt")
_MAX_GUIDE_CHARS = 256 * 1024


def _normalize_include(include_name: str) -> str:
    name = include_name.strip().strip("'\"<>").replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name


class EspIdfSdkProbe:
    """只读扫描已安装 ESP-IDF 的 include 树；不启动进程、不写文件。"""

    def resolve_include(
        self,
        include_name: str,
        idf_path: str | None,
    ) -> SdkIncludeResolution:
        normalized = _normalize_include(include_name)
        root = Path(idf_path) if idf_path else None
        try:
            exists, candidates = self._scan(root, normalized)
        except OSError:
            exists, candidates = False, []
        return SdkIncludeResolution(
            include_name=normalized,
            exists=exists,
            candidates=candidates,
            searched_root=str(root) if root is not None else None,
        )

    def search_migration(
        self,
        api_name: str,
        idf_path: str | None,
        limit: int = 3,
    ) -> list[SdkMigrationSnippet]:
        """在已安装 ESP-IDF 的迁移指南里检索与 *api_name* 相关的摘录。"""

        name = api_name.strip()
        if not name or idf_path is None or limit < 1:
            return []
        root = Path(idf_path)
        base = root / _MIGRATION_GUIDES_DIR
        snippets: list[SdkMigrationSnippet] = []
        try:
            if not base.is_dir():
                return snippets
            for guide in sorted(base.rglob("*")):
                if not guide.is_file():
                    continue
                if guide.suffix.casefold() not in _MIGRATION_EXTS:
                    continue
                text = self._read_guide(guide)
                if text is None:
                    continue
                window = self._snippet_around(text, name)
                if window is None:
                    continue
                snippets.append(
                    SdkMigrationSnippet(
                        guide=guide.relative_to(root).as_posix(),
                        snippet=window,
                    )
                )
                if len(snippets) >= limit:
                    break
        except OSError:
            pass
        return snippets

    @staticmethod
    def _read_guide(guide: Path) -> str | None:
        try:
            text = guide.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if len(text) > _MAX_GUIDE_CHARS:
            text = text[:_MAX_GUIDE_CHARS]
        return text

    @staticmethod
    def _snippet_around(text: str, needle: str, width: int = 500) -> str | None:
        index = text.casefold().find(needle.casefold())
        if index < 0:
            return None
        start = max(0, index - width // 2)
        end = min(len(text), index + width // 2)
        window = text[start:end]
        first_newline = window.find("\n")
        last_newline = window.rfind("\n")
        if first_newline != -1 and last_newline > first_newline:
            window = window[first_newline + 1 : last_newline]
        return " ".join(window.split())[:width] or None

    @classmethod
    def _scan(cls, root: Path | None, normalized: str) -> tuple[bool, list[str]]:
        if root is None:
            return False, []
        components = root / "components"
        if not components.is_dir():
            return False, []
        parts = normalized.split("/")
        top = parts[0]
        tail = "/".join(parts[1:]) if len(parts) > 1 else ""
        exists = cls._header_exists(components, top, normalized, tail)
        candidates = (
            [] if exists else cls._header_candidates(components, top, normalized)
        )
        return exists, candidates

    @staticmethod
    def _header_exists(
        components: Path,
        top: str,
        normalized: str,
        tail: str,
    ) -> bool:
        # 新版布局 components/<top>/include/<normalized>；旧版 components/<top>/include/<tail>。
        top_dir = components / top / "include"
        if top_dir.is_dir() and (
            (top_dir / normalized).is_file()
            or (tail and (top_dir / tail).is_file())
        ):
            return True
        for component in components.iterdir():
            include = component / "include"
            if not include.is_dir():
                continue
            if (include / normalized).is_file() or (
                tail and (include / tail).is_file()
            ):
                return True
        return False

    @staticmethod
    def _header_candidates(
        components: Path,
        top: str,
        normalized: str,
    ) -> list[str]:
        stem = Path(normalized).stem.casefold()
        if not stem:
            return []
        include_roots: list[Path] = []
        top_dir = components / top / "include"
        if top_dir.is_dir():
            include_roots.append(top_dir)
        seen = {path for path in include_roots}
        for component in components.iterdir():
            include = component / "include"
            if include.is_dir() and include not in seen:
                seen.add(include)
                include_roots.append(include)
        found: list[str] = []
        for include in include_roots:
            for header in include.rglob("*.h"):
                name = header.name.casefold()
                if name.startswith(stem) or stem in name:
                    rel = header.relative_to(include).as_posix()
                    if rel not in found:
                        found.append(rel)
                    if len(found) >= _MAX_CANDIDATES:
                        break
            if len(found) >= _MAX_CANDIDATES:
                break
        return found


__all__ = ["EspIdfSdkProbe"]
