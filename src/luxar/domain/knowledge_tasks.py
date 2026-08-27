"""Agent 可规划的外部知识库操作。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["list", "search", "upsert", "delete", "read_pdf", "import_pdf"]
    summary: str = Field(min_length=1, max_length=1000)
    query: str = ""
    source_uri: str = ""
    relative_path: str = ""
    file_path: str = ""
    title: str = ""
    content: str = Field(default="", max_length=2 * 1024 * 1024)
    document_id: str = ""
    missing_fields: list[str] = Field(default_factory=list)

    @property
    def mutating(self) -> bool:
        return self.action in {"upsert", "delete", "import_pdf"}
