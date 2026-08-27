"""运行时上下文：集中保存一次 Graph 调用所需的外部能力和项目路径。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver

from luxar.ports.espidf import EspIdfPort
from luxar.ports.espidf_device import EspIdfFlashPort, EspIdfMonitorPort
from luxar.ports.espidf_project import EspIdfProjectPort
from luxar.ports.log_analyst import LogAnalystPort
from luxar.ports.idf_examples import EspIdfExampleLibrary
from luxar.ports.planner import Planner
from luxar.ports.project_analyzer import ProjectAnalyzer
from luxar.ports.firmware_editor import FirmwareEditor
from luxar.ports.requirement_parser import RequirementParser
from luxar.ports.repair_planner import RepairPlanner
from luxar.ports.workspace import WorkspacePort
from luxar.database.persistence import PersistencePort
from luxar.knowledge import KnowledgeService
from luxar.ports.knowledge_tasks import KnowledgeTaskParser
from luxar.document_reader import PdfDocumentReader, PdfProgressReporter
from luxar.ports.document_analysis import PdfTechnicalAnalyzer
from luxar.ports.knowledge_extraction import KnowledgeAtomExtractor
from luxar.ports.knowledge_answering import KnowledgeAnswerer


@dataclass(frozen=True)
class RuntimeContext:
    # frozen=True 防止工作流运行中意外替换依赖；这些对象由启动代码统一注入。
    # Context 不进入 State/checkpoint，因此 API 客户端、密钥和文件工具不会被持久化。
    requirement_parser: RequirementParser
    planner: Planner
    espidf: EspIdfPort
    project_path: Path
    repair_planner: RepairPlanner
    workspace: WorkspacePort
    project_creator: EspIdfProjectPort
    # 目标芯片的可选显式配置；为 None 时创建节点回退到 requirement.target。
    target_chip: str | None
    flasher: EspIdfFlashPort
    monitor: EspIdfMonitorPort
    log_analyst: LogAnalystPort
    # 烧录与监控共用的串口名；None 表示未配置，节点给出脱敏错误。
    serial_port: str | None
    # 串口日志采集窗口秒数；超时是监控的正常结束方式。
    monitor_timeout_seconds: int
    # interrupt() 需要 checkpointer；Web/CLI 生命周期注入 SqliteSaver，
    # 仅测试或显式无持久化调用回退 InMemorySaver。
    checkpointer: BaseCheckpointSaver
    # Project analysis is shared by inspection and firmware implementation.
    project_analyzer: ProjectAnalyzer | None = None
    firmware_editor: FirmwareEditor | None = None
    example_library: EspIdfExampleLibrary | None = None
    persistence: PersistencePort | None = None
    project_key: str | None = None
    knowledge_service: KnowledgeService | None = None
    knowledge_task_parser: KnowledgeTaskParser | None = None
    document_reader: PdfDocumentReader | None = None
    pdf_progress_reporter: PdfProgressReporter | None = None
    document_analyzer: PdfTechnicalAnalyzer | None = None
    knowledge_extractor: KnowledgeAtomExtractor | None = None
    knowledge_answerer: KnowledgeAnswerer | None = None
    # 正式 Web/CLI 开启；底层批处理或旧测试夹具可显式关闭中断。
    interactive_workflow: bool = False
