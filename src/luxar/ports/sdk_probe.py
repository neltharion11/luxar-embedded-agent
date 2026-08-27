"""Port for grounding build failures against the installed SDK."""

from __future__ import annotations

from typing import Protocol

from luxar.domain.agent.sdk_probe import (
    SdkIncludeResolution,
    SdkMigrationSnippet,
)


class SdkProbePort(Protocol):
    """只读能力：判定头文件存在性、给出替代候选，并检索迁移指南片段。"""

    def resolve_include(
        self,
        include_name: str,
        idf_path: str | None,
    ) -> SdkIncludeResolution: ...

    def search_migration(
        self,
        api_name: str,
        idf_path: str | None,
        limit: int = 3,
    ) -> list[SdkMigrationSnippet]: ...


__all__ = ["SdkProbePort"]
