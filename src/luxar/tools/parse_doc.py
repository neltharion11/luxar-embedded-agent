from __future__ import annotations

from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.knowledge_base import KnowledgeBase
from luxar.core.pdf_parser import PDFParser


def run_parse_doc(
    config: AgentConfig,
    project_root: str,
    source_path: str,
    query: str = "",
    chunk_size: int = 1200,
    overlap: int = 120,
):
    root = Path(project_root).resolve()
    parser = PDFParser()
    result = parser.parse(
        source_path=source_path,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    knowledge_root = root / config.agent.driver_library / "knowledge_base"
    kb = KnowledgeBase(knowledge_root)
    if result.success:
        kb.store_document(result)
    return {
        "parse_result": result.model_dump(mode="json"),
        "knowledge_base": {
            "root": str(knowledge_root),
            "stats": kb.stats(),
            "query": query,
            "summary": kb.summarize_query(query) if query else result.summary,
            "matches": [chunk.model_dump(mode="json") for chunk in kb.search(query)] if query else [],
        },
    }

