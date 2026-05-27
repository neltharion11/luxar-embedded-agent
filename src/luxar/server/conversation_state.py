from __future__ import annotations

from dataclasses import dataclass, field

from luxar.core.conversation_store import ConversationStore


@dataclass
class ConversationState:
    store: ConversationStore | None = None
    cache: dict[str, list[dict]] = field(default_factory=dict)

    def get(self, project: str) -> list[dict]:
        if project not in self.cache:
            self.cache[project] = self.store.load(project) if self.store else []
        return self.cache[project]

    def save(self, project: str) -> None:
        if self.store and project in self.cache:
            self.store.save(project, self.cache[project])

    def close(self) -> None:
        if self.store:
            try:
                self.store.close()
            except Exception:
                pass
        self.store = None

    def reset_store(self, store: ConversationStore | None) -> None:
        self.close()
        self.store = store
