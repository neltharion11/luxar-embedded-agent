from __future__ import annotations


def default_provenance(source: str = "agent-managed") -> dict[str, str]:
    return {"source": source, "mode": "draft"}
