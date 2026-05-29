from __future__ import annotations

from luxar.core.config_manager import ConfigManager
from luxar.core.document_engineering import DocumentEngineeringAnalyzer


def analyze_document_engineering(
    *,
    docs: list[str],
    query: str = "",
    cm: ConfigManager,
) -> dict:
    """Parse attached documents and extract engineering context."""
    knowledge_root = cm.driver_library_root() / "knowledge_base"
    analyzer = DocumentEngineeringAnalyzer(knowledge_root)
    context = analyzer.analyze(docs=docs, query=query)
    return {
        "success": True,
        "source_documents": context.source_documents,
        "document_summary": context.document_summary,
        "pin_requirements": [pin.model_dump(mode="json") for pin in context.pin_requirements],
        "bus_requirements": [bus.model_dump(mode="json") for bus in context.bus_requirements],
        "protocol_frames": [frame.model_dump(mode="json") for frame in context.protocol_frames],
        "register_hints": context.register_hints,
        "bringup_sequence": [step.model_dump(mode="json") for step in context.bringup_sequence],
        "timing_constraints": context.timing_constraints,
        "integration_notes": context.integration_notes,
        "risk_notes": context.risk_notes,
        "parse_errors": context.parse_errors,
    }