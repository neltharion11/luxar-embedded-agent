from __future__ import annotations

from pathlib import Path
import re


_TASK_PROGRESS_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcurrent progress\b",
        r"\btask progress\b",
        r"\bnext step\b",
        r"\bremaining\b",
        r"\btodo\b",
        r"\bin progress\b",
        r"\bcompleted\b",
        r"\bstatus update\b",
        r"\bbuild passed\b",
        r"\bbuild failed\b",
        r"\bflash failed\b",
        r"\bmonitor failed\b",
        r"\bdebug loop\b",
        r"\bworkflow step\b",
        r"\bplan-only\b",
    )
]


class MemoryManager:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory_path = self.root / "MEMORY.md"
        self.user_path = self.root / "USER.md"
        for path in (self.memory_path, self.user_path):
            if not path.exists():
                path.write_text("", encoding="utf-8")

    def read(self, target: str = "memory") -> dict[str, str]:
        path = self.memory_path if target != "user" else self.user_path
        return {"target": target, "content": path.read_text(encoding="utf-8")}

    def write(self, content: str, target: str = "memory", append: bool = True) -> dict[str, object]:
        blocked_reason = self._blocked_reason(content)
        if blocked_reason:
            path = self.memory_path if target != "user" else self.user_path
            return {
                "success": False,
                "blocked": True,
                "target": target,
                "path": str(path),
                "error": blocked_reason,
            }
        path = self.memory_path if target != "user" else self.user_path
        if append and path.read_text(encoding="utf-8").strip():
            updated = path.read_text(encoding="utf-8") + "\n" + content.strip() + "\n"
        else:
            updated = content.strip() + ("\n" if content.strip() else "")
        path.write_text(updated, encoding="utf-8")
        return {"success": True, "target": target, "path": str(path)}

    def search(self, query: str) -> list[dict[str, str]]:
        query_norm = query.strip().lower()
        results: list[dict[str, str]] = []
        for target, path in (("memory", self.memory_path), ("user", self.user_path)):
            content = path.read_text(encoding="utf-8")
            if query_norm and query_norm in content.lower():
                results.append({"target": target, "path": str(path), "content": content})
        return results

    def _blocked_reason(self, content: str) -> str:
        normalized = content.strip()
        if not normalized:
            return ""
        for pattern in _TASK_PROGRESS_PATTERNS:
            if pattern.search(normalized):
                return (
                    "Refused to write transient task progress into durable memory. "
                    "Store stable facts such as user preferences, board conventions, or toolchain facts instead."
                )
        return ""
