from __future__ import annotations

from pathlib import Path


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
