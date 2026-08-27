from __future__ import annotations

from typing import Protocol

from luxar.domain.knowledge_tasks import KnowledgeTask


class KnowledgeTaskParser(Protocol):
    def parse(self, task_text: str) -> KnowledgeTask: ...

