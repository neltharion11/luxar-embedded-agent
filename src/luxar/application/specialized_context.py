"""Narrow runtime context for project inspection and knowledge operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver

from luxar.database.persistence import PersistencePort
from luxar.document_reader import PdfDocumentReader, PdfProgressReporter
from luxar.knowledge import KnowledgeService
from luxar.ports.document_analysis import PdfTechnicalAnalyzer
from luxar.ports.knowledge_tasks import KnowledgeTaskParser
from luxar.ports.knowledge_extraction import KnowledgeAtomExtractor
from luxar.ports.knowledge_answering import KnowledgeAnswerer
from luxar.ports.project_analyzer import ProjectAnalyzer
from luxar.ports.workspace import WorkspacePort


@dataclass(frozen=True)
class SpecializedRuntimeContext:
    project_path: Path
    workspace: WorkspacePort
    checkpointer: BaseCheckpointSaver
    target_chip: str | None = None
    project_analyzer: ProjectAnalyzer | None = None
    persistence: PersistencePort | None = None
    project_key: str | None = None
    knowledge_service: KnowledgeService | None = None
    knowledge_task_parser: KnowledgeTaskParser | None = None
    document_reader: PdfDocumentReader | None = None
    pdf_progress_reporter: PdfProgressReporter | None = None
    document_analyzer: PdfTechnicalAnalyzer | None = None
    knowledge_extractor: KnowledgeAtomExtractor | None = None
    knowledge_answerer: KnowledgeAnswerer | None = None


__all__ = ["SpecializedRuntimeContext"]
