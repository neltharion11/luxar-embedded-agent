"""Typed wrappers that expose existing LUXAR ports to the continuous Agent."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from luxar.application.project_analysis import analyze_current_project
from luxar.application.tool_registry import ToolRegistry
from luxar.adapters.font_bitmap import FontBitmapError, extract_font_bitmap
from luxar.adapters.font_selfcheck import (
    DISPLAY_SELFCHECK_TEMPLATES,
    verify_display_selfcheck,
)
from luxar.adapters.local_driver_library import DriverLibraryError
from luxar.database.persistence import PersistencePort
from luxar.domain.agent.code_changes import ChangeBundle, ChangeBundleError
from luxar.domain.continuous_agent.failures import ContinuousAgentFailure
from luxar.domain.continuous_agent.steps import AgentToolDescriptor
from luxar.domain.continuous_agent.tools import ToolResult
from luxar.domain.drivers import DriverPublishSpec, driver_verification_from_result
from luxar.ports.agent_tool import (
    AgentToolExecutionContext,
    ToolExecutionLedgerPort,
)
from luxar.ports.code_executor import CodeExecutorPort
from luxar.ports.espidf import EspIdfPort
from luxar.ports.espidf_device import EspIdfFlashPort, EspIdfMonitorPort
from luxar.ports.driver_library import DriverLibraryPort
from luxar.ports.project_analyzer import ProjectAnalyzer
from luxar.ports.workspace import WorkspacePort
from luxar.ports.workspace_errors import WorkspaceError


class KnowledgeSearchPort(Protocol):
    def search(
        self,
        *,
        project_key: str,
        query: str,
        limit: int = 6,
    ) -> list[object]: ...

    def entity_candidates(self) -> list[dict[str, object]]: ...

    def register_entity(self, *, entity: object, replace: bool = False) -> bool: ...

    def list_entities(self) -> list[object]: ...


class KnowledgeWritePort(Protocol):
    def ingest_pdf(
        self,
        *,
        project_key: str,
        source_uri: str,
        title: str,
        path: Path,
        reader: object | None = None,
        extractor: object | None = None,
        progress_reporter: object | None = None,
    ) -> object: ...


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _FlashArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    serial_port: str = Field(min_length=1, max_length=80)


class _MonitorArguments(_FlashArguments):
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class _ApplyChangeBundleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    bundle: ChangeBundle


class _KnowledgeSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=4_000)
    limit: int = Field(default=6, ge=1, le=20)


class _PdfReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    file_path: str = Field(min_length=1, max_length=1_024)
    max_characters: int = Field(default=60_000, ge=1_000, le=200_000)


class _KnowledgeImportPdfArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    file_path: str = Field(min_length=1, max_length=1_024)
    title: str = Field(default="", max_length=240)


class _FontExtractArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=500)
    font: str = Field(default="msyhbd", min_length=1, max_length=300)
    width: int | None = Field(default=None, ge=1, le=128)
    height: int | None = Field(default=None, ge=1, le=128)
    controller: str | None = Field(default=None, min_length=1, max_length=60)
    scan: Literal["row", "column"] | None = None
    bit_order: Literal["msb", "lsb"] | None = None
    invert: bool | None = None
    align: Literal["center", "left"] = "center"
    ascii_half_width: bool = False
    ascii_font: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description=(
            "混合字库（ascii_half_width=true 且含中文）时，ASCII 字形改用的 "
            "u8g2 内置点阵字体（如 u8g2_5x7/u8g2_8x13），像素级精确、不走 TTF "
            "降采样；不传则 ASCII 与中文都用 font 指定的字体渲染"
        ),
    )
    array_name: str | None = Field(default=None, min_length=1, max_length=80)
    file_path: str | None = Field(default=None, min_length=1, max_length=1_024)


class _VerifyLine(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=500)
    x: int = Field(ge=0, le=4096)
    y: int = Field(ge=0, le=4096)


class _DisplayVerifyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    header_path: str = Field(min_length=1, max_length=1_024)
    lines: list[_VerifyLine] = Field(min_length=1, max_length=32)
    screen_width: int = Field(default=128, ge=8, le=4096)
    screen_height: int = Field(default=64, ge=8, le=4096)
    controller: str | None = Field(
        default=None,
        min_length=1,
        max_length=60,
        description=(
            "目标控制器名（如 sh1106/ssd1306）。传入时按芯片规格校验字模"
            "打包布局与控制器硬件约定是否一致，冲突即报错（把位序/行序约定"
            "错误在自检阶段暴露，而不是等真机乱码）"
        ),
    )
    actual_crc32: str | None = Field(
        default=None,
        min_length=8,
        max_length=8,
        pattern=r"^[0-9A-Fa-f]{8}$",
    )
    actual_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9A-Fa-f]{64}$",
    )


class _ChipSpecDraftArguments(BaseModel):
    """chip.spec.draft 参数：从提取的芯片事实起草规格（verified=unverified）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    controller: str = Field(min_length=1, max_length=60)
    aliases: list[str] = Field(default_factory=list, max_length=16)
    family: str = Field(default="", max_length=60)
    facts: dict[str, object] = Field(default_factory=dict)
    layout: dict[str, object] = Field(
        description="必需：{'scan': 'row'|'column', 'bit_order': 'msb'|'lsb', 'invert': bool}"
    )
    screen: dict[str, object] = Field(
        description="必需：{'width': int, 'height': int}"
    )
    column_offset: int = Field(default=0, ge=0, le=255)
    init: list[dict[str, object]] = Field(
        default_factory=list,
        description="初始化命令序列：[{'cmd': 0xXX, 'args': [...]}]",
    )
    display_options: dict[str, object] = Field(default_factory=dict)
    driver_template: str = Field(default="", max_length=120)
    diagnostics: list[dict[str, object]] = Field(default_factory=list)


class _ChipSpecVerifyArguments(BaseModel):
    """chip.spec.verify 参数：追加一条分层验证记录并固化。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    controller: str = Field(min_length=1, max_length=60)
    level: Literal["L1", "L2", "L3", "L4"]
    task: str = Field(min_length=1, max_length=240)
    date: str = Field(default="", max_length=40)
    project: str = Field(default="", max_length=120)
    pattern: str = Field(default="", max_length=120)
    result: Literal["pass", "fail"] = "pass"
    user_confirmed: bool = False
    evidence: str = Field(default="", max_length=4_000)
    hardware: str = Field(default="", max_length=300)
    expected_crc: str = Field(default="", max_length=32)
    actual_crc: str = Field(default="", max_length=32)


class _EntityRegisterArguments(BaseModel):
    """knowledge.entity.register 参数：注册一个硬件实体（chip 或 device）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["chip", "device"]
    name: str = Field(min_length=1, max_length=120)
    chip_ref: str = Field(
        default="",
        max_length=80,
        description="device 必填：所引用 chip 实体的 entity_id",
    )
    source_uris: list[str] = Field(
        default_factory=list,
        max_length=32,
        description="描述本实体的来源文档 URI 列表（可多份）",
    )
    aliases: list[str] = Field(default_factory=list, max_length=16)
    notes: str = Field(default="", max_length=500)


class _DriverReference(BaseModel):
    """模型声明引用的一条知识原子（硬件字节的手册出处）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    knowledge_id: str = Field(min_length=1, max_length=120)
    subject: str = Field(default="", max_length=160)
    #: 原子归属的实体（chip/device entity_id）；空 = 未归属，不作依据
    entity_id: str = Field(default="", max_length=80)
    #: 手册原文摘录（source_excerpt，逐字来自手册）
    excerpt: str = Field(min_length=1, max_length=8000)


class _DriverVerifyArguments(BaseModel):
    """driver.verify 参数：校验代码的硬件字节都有手册出处。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = Field(min_length=1, max_length=200_000)
    references: list[_DriverReference] = Field(
        min_length=1,
        max_length=64,
        description=(
            "模型声明依据的知识原子（从 knowledge.search 结果取 subject/"
            "entity_id/excerpt 填入）。代码中的每个硬件字节必须能在这些原子的"
            "excerpt（手册原文）中找到，否则报违规"
        ),
    )


class _DriverSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(default="", max_length=4_000)
    hardware: str = Field(default="", max_length=160)
    protocol: str = Field(default="", max_length=80)
    target_chip: str = Field(default="", max_length=80)
    limit: int = Field(default=5, ge=1, le=20)


class _DriverReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    driver_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    version: str | None = Field(default=None, max_length=64)


class _DriverPublishArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    driver_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$",
    )
    name: str = Field(min_length=1, max_length=160)
    vendor: str = Field(default="", max_length=120)
    hardware: str = Field(min_length=1, max_length=160)
    protocols: list[str] = Field(min_length=1, max_length=16)
    targets: list[str] = Field(default_factory=list, max_length=32)
    description: str = Field(default="", max_length=2_000)
    file_paths: list[str] = Field(min_length=1, max_length=64)


def _project_path(context: AgentToolExecutionContext) -> Path:
    if context.project_path is None:
        raise ValueError("Agent tool project path is unavailable")
    return context.project_path


def _resolve_pdf_source(
    raw_path: str,
    context: AgentToolExecutionContext,
) -> Path | None:
    """Resolve a user-supplied PDF path within the current project boundary.

    Absolute paths are allowed unchanged (the model only learns them from the
    user's own message); project-relative paths must stay inside the project.
    """
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate.expanduser().resolve()
    root = _project_path(context)
    source = (root / candidate).resolve()
    try:
        source.relative_to(root)
    except ValueError:
        return None
    return source


def _resolve_export_path(
    raw_path: str,
    context: AgentToolExecutionContext,
) -> Path | None:
    """把 font.export 的目标头文件解析到当前工程内（写路径，越界即拒绝）。"""
    if not raw_path.strip():
        return None
    root = _project_path(context).resolve()
    candidate = Path(raw_path.strip())
    if candidate.is_absolute():
        resolved = candidate.expanduser().resolve()
    else:
        resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    if resolved.suffix.lower() not in {
        ".h",
        ".hpp",
        ".c",
        ".cpp",
    }:
        return None
    return resolved


class WorkspaceReadProjectTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="workspace.read_project",
        description=(
            "读取当前项目内受控的源码和 ESP-IDF 配置文件，每个文件带 "
            "path/content/sha256（磁盘原始字节的 SHA-256）。修改已存在文件时，"
            "apply_change_bundle 的 expected_sha256 必须精确复制该文件的 sha256 值"
        ),
        input_schema=_NoArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self, workspace: WorkspacePort) -> None:
        self._workspace = workspace

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments
        files = self._workspace.read_project_files(_project_path(context))
        return ToolResult(
            success=True,
            output={
                "file_count": len(files),
                "files": [item.model_dump(mode="json") for item in files],
            },
            evidence_ids=[f"source:{item.path}" for item in files],
        )


class ProjectInspectTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="project.inspect",
        description="读取并分析当前项目结构、入口、已实现能力、缺口和风险",
        input_schema=_NoArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(
        self,
        *,
        workspace: WorkspacePort,
        analyzer: ProjectAnalyzer | None = None,
        persistence: PersistencePort | None = None,
        target_chip: str | None = None,
    ) -> None:
        self._workspace = workspace
        self._analyzer = analyzer
        self._persistence = persistence
        self._target_chip = target_chip

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments
        analysis = analyze_current_project(
            project_path=_project_path(context),
            target_chip=self._target_chip,
            workspace=self._workspace,
            analyzer=self._analyzer,
            persistence=self._persistence,
            project_key=context.project_key,
        )
        return ToolResult(
            success=True,
            output=analysis.model_dump(mode="json"),
            evidence_ids=[f"source:{path}" for path in analysis.evidence_paths],
        )


class WorkspaceApplyChangeBundleTool:
    input_model = _ApplyChangeBundleArguments
    descriptor = AgentToolDescriptor(
        name="workspace.apply_change_bundle",
        description=(
            "事务式应用经过路径白名单、快照哈希和既有能力保护约束的代码变更包"
        ),
        input_schema=_ApplyChangeBundleArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def __init__(self, executor: CodeExecutorPort) -> None:
        self._executor = executor

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _ApplyChangeBundleArguments)
        try:
            validation = self._executor.execute(
                _project_path(context),
                arguments.bundle,
            )
        except ChangeBundleError as error:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="validation",
                    code=error.category,
                    message=str(error),
                    retryable=error.category in {"conflict", "stale_snapshot"},
                    details={"paths": list(error.details)},
                ),
            )
        except WorkspaceError as error:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code=error.category,
                    message=error.message,
                    retryable=error.retryable,
                ),
            )
        return ToolResult(
            success=True,
            output=validation.model_dump(mode="json"),
            evidence_ids=[
                f"source:{path}" for path in validation.changed_files
            ],
        )


class KnowledgeSearchTool:
    input_model = _KnowledgeSearchArguments
    descriptor = AgentToolDescriptor(
        name="knowledge.search",
        description=(
            "在整个共享知识库中检索相关资料和来源片段；"
            "知识库不按项目隔离，任意项目入库的文档都可被检索"
        ),
        input_schema=_KnowledgeSearchArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self, knowledge: KnowledgeSearchPort) -> None:
        self._knowledge = knowledge

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _KnowledgeSearchArguments)
        matches = self._knowledge.search(
            project_key=context.project_key,
            query=arguments.query,
            limit=arguments.limit,
        )
        payloads = [
            asdict(match)
            if hasattr(match, "__dataclass_fields__")
            else dict(match)  # type: ignore[arg-type]
            for match in matches
        ]
        return ToolResult(
            success=True,
            output={"matches": payloads, "count": len(payloads)},
            evidence_ids=[
                f"knowledge:{payload.get('document_id')}:{payload.get('ordinal')}"
                for payload in payloads
                if payload.get("document_id") is not None
            ],
        )


def _driver_failure(error: DriverLibraryError) -> ToolResult:
    retryable = error.code in {"write_failed"}
    return ToolResult(
        success=False,
        failure=ContinuousAgentFailure(
            category="tool" if retryable else "validation",
            code=f"driver_{error.code}",
            message=str(error),
            retryable=retryable,
        ),
    )


class DriverSearchTool:
    input_model = _DriverSearchArguments
    descriptor = AgentToolDescriptor(
        name="driver.search",
        description=(
            "在应用级公共驱动库中按硬件、通信协议和目标芯片检索可复用驱动；"
            "为明确硬件和协议编写驱动前必须优先调用"
        ),
        input_schema=_DriverSearchArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self, library: DriverLibraryPort) -> None:
        self._library = library

    def execute(self, arguments: BaseModel, context: AgentToolExecutionContext) -> ToolResult:
        del context
        assert isinstance(arguments, _DriverSearchArguments)
        try:
            drivers = self._library.search(
                query=arguments.query,
                hardware=arguments.hardware,
                protocol=arguments.protocol,
                target_chip=arguments.target_chip,
                limit=arguments.limit,
            )
        except DriverLibraryError as error:
            return _driver_failure(error)
        payloads = [item.model_dump(mode="json") for item in drivers]
        return ToolResult(
            success=True,
            output={"drivers": payloads, "count": len(payloads)},
            evidence_ids=[
                f"driver:{item.driver_id}:{item.version}" for item in drivers
            ],
        )


class DriverReadTool:
    input_model = _DriverReadArguments
    descriptor = AgentToolDescriptor(
        name="driver.read",
        description="读取一个公共驱动包的清单和完整受限源码，供当前工程复用或适配",
        input_schema=_DriverReadArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self, library: DriverLibraryPort) -> None:
        self._library = library

    def execute(self, arguments: BaseModel, context: AgentToolExecutionContext) -> ToolResult:
        del context
        assert isinstance(arguments, _DriverReadArguments)
        try:
            package = self._library.read(arguments.driver_id, arguments.version)
        except DriverLibraryError as error:
            return _driver_failure(error)
        manifest = package.manifest
        return ToolResult(
            success=True,
            output=package.model_dump(mode="json"),
            evidence_ids=[f"driver:{manifest.driver_id}:{manifest.version}"],
        )


class DriverPublishTool:
    input_model = _DriverPublishArguments
    descriptor = AgentToolDescriptor(
        name="driver.publish",
        description=(
            "仅当用户明确要求保存、发布或加入公共驱动库时，把当前项目中的"
            "指定相对路径发布为不可变驱动版本；该跨项目写入必须审批"
        ),
        input_schema=_DriverPublishArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def __init__(
        self,
        library: DriverLibraryPort,
        persistence: PersistencePort | None,
    ) -> None:
        self._library = library
        self._persistence = persistence

    def execute(self, arguments: BaseModel, context: AgentToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, _DriverPublishArguments)
        try:
            latest = (
                self._persistence.get_latest_completed_run(context.project_key)
                if self._persistence is not None
                else None
            )
            verification = driver_verification_from_result(
                latest.result if latest is not None else None
            )
            manifest = self._library.publish(
                project_path=_project_path(context),
                project_key=context.project_key,
                spec=DriverPublishSpec.model_validate(arguments.model_dump()),
                verification=verification,
            )
        except DriverLibraryError as error:
            return _driver_failure(error)
        return ToolResult(
            success=True,
            output={"published": True, "driver": manifest.model_dump(mode="json")},
            evidence_ids=[f"driver:{manifest.driver_id}:{manifest.version}"],
        )


class PdfReadTool:
    input_model = _PdfReadArguments
    descriptor = AgentToolDescriptor(
        name="pdf.read",
        description=(
            "读取用户明确指定的本地 PDF 文件并返回其全文内容，附带页数、"
            "章节划分与截断信息；只读取，不写入任何内容"
        ),
        input_schema=_PdfReadArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self, reader: object | None = None) -> None:
        self._reader = reader

    def validate_policy(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ContinuousAgentFailure | None:
        assert isinstance(arguments, _PdfReadArguments)
        if _resolve_pdf_source(arguments.file_path, context) is None:
            return ContinuousAgentFailure(
                category="policy",
                code="pdf_path_outside_project",
                message="PDF 相对路径越出当前项目",
                retryable=False,
                details={"file_path": arguments.file_path},
            )
        return None

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _PdfReadArguments)
        raw_path = arguments.file_path.strip()
        source = _resolve_pdf_source(raw_path, context)
        if source is None:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="policy",
                    code="pdf_path_outside_project",
                    message="PDF 相对路径越出当前项目",
                    retryable=False,
                    details={"file_path": arguments.file_path},
                ),
            )
        if not source.is_file():
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code="pdf_not_found",
                    message="指定的 PDF 文件不存在",
                    retryable=True,
                    details={"file_path": str(source)},
                ),
            )
        if self._reader is None:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="service",
                    code="pdf_reader_unavailable",
                    message="PDF 读取器尚未配置",
                    retryable=False,
                ),
            )
        from luxar.document_reader import iter_pdf_batches

        try:
            batches = list(iter_pdf_batches(self._reader, source))
        except Exception as error:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code="pdf_read_failed",
                    message=f"PDF 读取失败：{error}",
                    retryable=True,
                ),
            )
        content = "\n\n".join(batch.content for batch in batches)
        total_pages = batches[-1].total_pages if batches else 0
        section_titles = [
            batch.section_title
            for batch in batches
            if batch.section_title.strip()
        ][:50]
        truncated = len(content) > arguments.max_characters
        return ToolResult(
            success=True,
            output={
                "read": True,
                "title": source.stem,
                "file_path": str(source),
                "total_pages": total_pages,
                "batches": len(batches),
                "sections": len(batches),
                "characters": len(content),
                "truncated": truncated,
                "section_titles": section_titles,
                "content": content[: arguments.max_characters],
            },
            evidence_ids=[f"pdf:{raw_path}"],
        )


class KnowledgeImportPdfTool:
    input_model = _KnowledgeImportPdfArguments
    descriptor = AgentToolDescriptor(
        name="knowledge.import",
        description=(
            "读取一份用户指定的本地 PDF，按章节提取其中的具体知识并写入"
            "当前项目的知识库（写库操作，需要用户批准）"
        ),
        input_schema=_KnowledgeImportPdfArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def __init__(
        self,
        service: KnowledgeWritePort,
        reader: object | None = None,
    ) -> None:
        self._service = service
        self._reader = reader

    def validate_policy(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ContinuousAgentFailure | None:
        assert isinstance(arguments, _KnowledgeImportPdfArguments)
        if _resolve_pdf_source(arguments.file_path, context) is None:
            return ContinuousAgentFailure(
                category="policy",
                code="pdf_path_outside_project",
                message="PDF 相对路径越出当前项目",
                retryable=False,
                details={"file_path": arguments.file_path},
            )
        return None

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _KnowledgeImportPdfArguments)
        raw_path = arguments.file_path.strip()
        source = _resolve_pdf_source(raw_path, context)
        if source is None:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="policy",
                    code="pdf_path_outside_project",
                    message="PDF 相对路径越出当前项目",
                    retryable=False,
                    details={"file_path": arguments.file_path},
                ),
            )
        if not source.is_file():
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code="pdf_not_found",
                    message="指定的 PDF 文件不存在",
                    retryable=True,
                    details={"file_path": str(source)},
                ),
            )
        from luxar.document_reader import PdfReadProgress

        progress_reporter = context.progress_reporter

        def _on_progress(progress: PdfReadProgress) -> None:
            if progress_reporter is None:
                return
            progress_reporter(
                "progress",
                {
                    "stage": "knowledge_import",
                    "phase": progress.phase,
                    "message": progress.message,
                    "completed_pages": progress.completed_pages,
                    "total_pages": progress.total_pages,
                },
            )

        try:
            imported = self._service.ingest_pdf(
                project_key=context.project_key,
                source_uri=raw_path.replace("\\", "/"),
                title=arguments.title.strip() or source.stem,
                path=source,
                reader=self._reader,
                progress_reporter=_on_progress,
            )
        except Exception as error:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code="knowledge_import_failed",
                    message=f"写入知识库失败：{error}",
                    retryable=True,
                ),
            )
        document_ids = [
            item.document_id for item in getattr(imported, "documents", [])
        ]
        return ToolResult(
            success=True,
            output={
                "imported": True,
                "title": arguments.title.strip() or source.stem,
                "total_pages": getattr(imported, "total_pages", 0),
                "batches": getattr(imported, "batches", 0),
                "sections": getattr(imported, "batches", 0),
                "knowledge_units": getattr(imported, "knowledge_units", 0),
                "document_ids": document_ids,
            },
            evidence_ids=[
                f"knowledge:{document_id}" for document_id in document_ids
            ],
        )


class KnowledgeEntityCandidatesTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="knowledge.entity.candidates",
        description=(
            "检测知识库中描述同一硬件的文档组（关联候选）：扫描已入库原子的 "
            "scope（controller/device），同一标识符被多份文档声明即视为候选。"
            "典型场景：芯片手册 + 屏厂手册各写一半（命令定义 vs 初始化序列）。"
            "返回候选列表 [{scope_key, scope_value, documents:[{source_uri,title}]}]。"
            "只读探测——关联是否成立由用户确认（agent 不得自行合并，防止不同"
            "型号/参考设计被误关联）。确认后可用 knowledge.entity.register 注册"
            "chip/device 实体并聚合知识。只读，不写文件"
        ),
        input_schema=_NoArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self, knowledge: KnowledgeSearchPort) -> None:
        self._knowledge = knowledge

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments
        candidates = self._knowledge.entity_candidates()
        return ToolResult(
            success=True,
            output={
                "candidates": candidates,
                "count": len(candidates),
                "note": (
                    "候选只表示'这些文档声明了同一标识符'，不表示一定是同一"
                    "硬件。请用 ask_user 向用户确认关联是否成立，确认后再用 "
                    "knowledge.entity.register 注册实体"
                ),
            },
            evidence_ids=[
                f"knowledge:entity_candidates"
            ],
        )


class KnowledgeEntityRegisterTool:
    input_model = _EntityRegisterArguments
    descriptor = AgentToolDescriptor(
        name="knowledge.entity.register",
        description=(
            "注册一个硬件实体（chip 芯片类 / device 硬件实例），把多份描述同一"
            "硬件的文档关联起来。kind=chip 注册芯片类（如 SH1106，所有使用它的"
            "硬件共享命令/寄存器/位序）；kind=device 注册具体模组（如 1.3寸横屏，"
            "必须 chip_ref 引用一个 chip 实体，自有 init 序列/分辨率/引脚）。"
            "source_uris=描述本实体的文档 URI 列表。**必须在用户确认关联后使用**"
            "（先 knowledge.entity.candidates 探测 → ask_user 确认 → 再注册），"
            "agent 不得自行合并不同文档。已存在同名实体时拒绝（防误覆盖）。"
            "写知识库需要用户批准"
        ),
        input_schema=_EntityRegisterArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def __init__(self, knowledge: KnowledgeSearchPort) -> None:
        self._knowledge = knowledge

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del context
        assert isinstance(arguments, _EntityRegisterArguments)
        from luxar.domain.hardware_entities import HardwareEntity, entity_id_for

        entity_id = entity_id_for(arguments.kind, arguments.name)
        entity = HardwareEntity(
            entity_id=entity_id,
            kind=arguments.kind,
            name=arguments.name,
            chip_ref=arguments.chip_ref or None,
            source_uris=tuple(arguments.source_uris),
            aliases=tuple(arguments.aliases),
            notes=arguments.notes,
        )
        try:
            registered = self._knowledge.register_entity(entity=entity)
        except Exception as error:
            return _chip_spec_failure(error, prefix="knowledge_entity")
        if not registered:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="validation",
                    code="knowledge_entity_exists",
                    message=(
                        f"实体已存在（{entity.entity_id}）：{arguments.name}。"
                        "如需修改请先确认来源，不应重复注册"
                    ),
                    retryable=False,
                    details={"entity_id": entity_id},
                ),
            )
        return ToolResult(
            success=True,
            output={
                "registered": True,
                "entity_id": entity_id,
                "kind": arguments.kind,
                "name": arguments.name,
                "chip_ref": arguments.chip_ref or None,
                "source_uris": list(arguments.source_uris),
                "note": (
                    "实体已注册。此后该实体的参数原子会按 scope 自动归属；"
                    "可用 knowledge.entity.knowledge 取回 device+chip 聚合知识"
                ),
            },
            evidence_ids=[f"knowledge:entity:{entity_id}"],
        )


class DriverVerifyTool:
    input_model = _DriverVerifyArguments
    descriptor = AgentToolDescriptor(
        name="driver.verify",
        description=(
            "引用式驱动校验：检查驱动 C 代码中的每个硬件字节（init 命令、寄存器"
            "地址、配置值）是否在**引用的知识原子**的手册原文（excerpt）中有出处。"
            "背景：模型写驱动代码的不可靠点在硬件事实（init 抄错芯片、寄存器记混），"
            "不在代码组织。本工具把'模型记忆'与'手册依据'分开——代码结构自由组织，"
            "但每个硬件字节必须引用 knowledge.search 检索到的参数原子，且能在其 "
            "excerpt（手册原文）中逐字定位，否则报违规（含行号与缺失字节）。"
            "用法：先用 knowledge.search / knowledge.entity.knowledge 检索相关"
            "参数原子（拿 subject/entity_id/excerpt），写完代码后把代码 + 引用原子"
            "传给本工具。references 每条含 knowledge_id/subject/entity_id/excerpt；"
            "entity_id 为空（未归属实体的原子）视为无手册依据，直接不通过。"
            "只读，不写文件"
        ),
        input_schema=_DriverVerifyArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del context
        assert isinstance(arguments, _DriverVerifyArguments)
        from luxar.adapters.driver_verify import ReferencedAtom, check_driver

        references = [
            ReferencedAtom(
                knowledge_id=ref.knowledge_id,
                excerpt=ref.excerpt,
                entity_id=ref.entity_id,
                subject=ref.subject,
            )
            for ref in arguments.references
        ]
        result = check_driver(arguments.code, references)
        output: dict[str, object] = {
            "ok": result.ok,
            "code_bytes": [f"0x{b:02X}" for b in result.code_bytes],
            "referenced_bytes": [f"0x{b:02X}" for b in result.referenced_bytes],
            "violations": [
                {
                    "line": v.line,
                    "byte": f"0x{v.byte_value:02X}",
                    "hint": v.hint,
                }
                for v in result.violations
            ],
            "unattributed_refs": list(result.unattributed_refs),
            "message": (
                "驱动代码的所有硬件字节都能在引用的手册原文中找到出处"
                if result.ok
                else "驱动代码存在无手册依据的硬件字节：请核对引用原子，"
                "或从手册/库源码补充引用"
            ),
        }
        return ToolResult(
            success=result.ok,
            output=output,
            evidence_ids=[
                f"driver.verify:{ref.knowledge_id}" for ref in arguments.references
            ],
            failure=(
                None
                if result.ok
                else ContinuousAgentFailure(
                    category="validation",
                    code="driver_byte_unreferenced",
                    message="驱动代码存在无手册依据的硬件字节",
                    retryable=True,
                    details={
                        "violations": output["violations"],
                        "unattributed_refs": output["unattributed_refs"],
                    },
                )
            ),
        )


def _font_failure(error: FontBitmapError) -> ToolResult:
    retryable = error.code in {"font_not_found", "font_rasterize_failed"}
    return ToolResult(
        success=False,
        failure=ContinuousAgentFailure(
            category="tool" if retryable else "validation",
            code=f"font_{error.code}",
            message=str(error),
            retryable=retryable,
            details=error.details,
        ),
    )


class FontExtractTool:
    input_model = _FontExtractArguments
    descriptor = AgentToolDescriptor(
        name="font.extract",
        description=(
            "按显示屏控制器的内存布局确定性生成字模（取模）C 代码，返回可直接嵌入驱动库的"
            "头文件内容；工具负责光栅化与位打包，模型绝不手写字模字节。"
            "text=要取模的字符（必填，可传完整文案，工具自动去重并按首次出现排序；"
            "必须来自用户明确要求显示的字符，禁止猜测字符集——用户没说显示什么字时，"
            "先用 ask_user 向用户询问具体字符）；"
            "font=字体（默认 msyhbd/微软雅黑粗体：笔画粗，16px 下 'e'/'w' 等"
            "小写字母清晰，最接近嵌入式点阵字体观感；其他：msyh(微软雅黑)/"
            "arialbd(Arial粗体)/consolab(等宽粗体)/simhei(黑体)/simsun(宋体)/"
            "consola/arial/cascadia_mono/noto_sans_sc；u8g2 内置点阵字体"
            "（纯 ASCII 等宽、像素级清晰，嵌入式标准观感）：u8g2_5x7/"
            "u8g2_6x10/u8g2_8x13/u8g2_10x20，含中文时不可用、需用 TTF 字体；"
            "或字体文件绝对路径）；"
            "width/height=字模单元尺寸：显式指定时为固定等宽字模（如 6x8/8x16），"
            "不指定时纯 ASCII 按实际字宽比例排版、含中文默认 16x16；"
            "controller=控制器名（内置芯片规格：ssd1306/ssd1315/sh1106=纵向逐列取模"
            "（位0=页顶 LSB），pcd8544/nokia5110/st7735/st7789/ili9341/ili9488/"
            "hd44780=横向逐行取模；每芯片的布局/屏幕/init/验证状态见 src/luxar/"
            "specs/chips/*.yaml），也可用 scan=row|column、bit_order=msb|lsb、"
            "invert=true(阴码) 单独覆盖；"
            "ascii_half_width=true 时生成混合中英文字库：ASCII 按字体实际字宽"
            "（比例 advance）取模并左对齐，'l' 窄、'w' 宽，拼接成单词间距均匀、"
            "字形不被压扁，中文取全宽；输出 GLYPH_WIDTHS/GLYPH_OFFSETS 表，"
            "驱动按宽度累加即可居中混排；"
            "ascii_font=u8g2 内置字体名（如 u8g2_5x7/u8g2_8x13）时，ASCII 改由"
            "u8g2 像素级点阵取模（不经过 TTF 降采样，小字号细笔画不失真），"
            "中文仍用 font 指定的字体；"
            "align=center|left 控制字形在单元内的对齐。"
            "控制器不在内置芯片规格（见 src/luxar/specs/chips/）时：先查芯片"
            "数据手册确定内存布局，再显式传 scan=row|column、bit_order=msb|lsb、"
            "invert 而不传 controller；位序约定必须真机验证。只读，不写文件"
        ),
        input_schema=_FontExtractArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del context
        assert isinstance(arguments, _FontExtractArguments)
        try:
            result = extract_font_bitmap(
                text=arguments.text,
                font=arguments.font,
                width=arguments.width,
                height=arguments.height,
                controller=arguments.controller,
                scan=arguments.scan,
                bit_order=arguments.bit_order,
                invert=arguments.invert,
                align=arguments.align,
                array_name=arguments.array_name,
                ascii_half_width=arguments.ascii_half_width,
                ascii_font=arguments.ascii_font,
            )
        except FontBitmapError as error:
            return _font_failure(error)
        except Exception:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code="font_rasterize_failed",
                    message="字模光栅化失败，请检查字体文件与参数",
                    retryable=True,
                ),
            )
        return ToolResult(
            success=True,
            output={
                "glyphs": [glyph.char for glyph in result.glyphs],
                "glyph_count": len(result.glyphs),
                "width": result.width,
                "height": result.height,
                "bytes_per_glyph": result.bytes_per_glyph,
                "total_bytes": result.total_bytes,
                "glyph_widths": result.glyph_widths,
                "ascii_half_width": result.ascii_half_width,
                "font": result.font_name,
                "font_path": result.font_path,
                "layout": result.layout.human,
                "array_name": result.array_name,
                "c_code": result.c_code,
            },
            evidence_ids=[
                f"font:{result.font_name}:{result.width}x{result.height}"
            ],
        )


class FontExportTool:
    input_model = _FontExtractArguments
    descriptor = AgentToolDescriptor(
        name="font.export",
        description=(
            "按显示屏控制器的内存布局确定性取模，并把生成的字模头文件写入当前工程"
            "（写文件需要用户批准）。text=要取模的字符（必填，可传完整文案，自动去重；"
            "必须来自用户明确要求显示的字符，禁止猜测字符集——用户没说显示什么字时，"
            "先用 ask_user 向用户询问具体字符）；"
            "file_path=工程内相对路径或工程内绝对路径，"
            "扩展名限 .h/.hpp/.c/.cpp；其余参数与 font.extract 相同（含 "
            "ascii_half_width 混合中英文字库，可用 ascii_font 指定 u8g2 内置"
            "点阵字体让 ASCII 像素级精确取模）。"
            "写入成功后文件即存在，无需再让模型转抄位图字节"
        ),
        input_schema=_FontExtractArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def validate_policy(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ContinuousAgentFailure | None:
        assert isinstance(arguments, _FontExtractArguments)
        target = _resolve_export_path(arguments.file_path or "", context)
        if target is None:
            return ContinuousAgentFailure(
                category="policy",
                code="font_path_outside_project",
                message="字体头文件路径必须位于当前工程内，且扩展名为 .h/.hpp/.c/.cpp",
                retryable=False,
                details={"file_path": arguments.file_path},
            )
        return None

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _FontExtractArguments)
        root = _project_path(context).resolve()
        target = _resolve_export_path(arguments.file_path or "", context)
        if target is None:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="policy",
                    code="font_path_outside_project",
                    message="字体头文件路径必须位于当前工程内，且扩展名为 .h/.hpp/.c/.cpp",
                    retryable=False,
                    details={"file_path": arguments.file_path},
                ),
            )
        try:
            result = extract_font_bitmap(
                text=arguments.text,
                font=arguments.font,
                width=arguments.width,
                height=arguments.height,
                controller=arguments.controller,
                scan=arguments.scan,
                bit_order=arguments.bit_order,
                invert=arguments.invert,
                align=arguments.align,
                array_name=arguments.array_name,
                ascii_half_width=arguments.ascii_half_width,
                ascii_font=arguments.ascii_font,
                project_path=root,
            )
        except FontBitmapError as error:
            return _font_failure(error)
        except Exception:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code="font_rasterize_failed",
                    message="字模光栅化失败，请检查字体文件与参数",
                    retryable=True,
                ),
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result.c_code, encoding="utf-8")
        except OSError as error:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code="font_write_failed",
                    message=f"写入字体头文件失败：{error}",
                    retryable=True,
                ),
            )
        relative = target.relative_to(root).as_posix()
        return ToolResult(
            success=True,
            output={
                "written": True,
                "file_path": relative,
                "glyphs": [glyph.char for glyph in result.glyphs],
                "glyph_count": len(result.glyphs),
                "width": result.width,
                "height": result.height,
                "bytes_per_glyph": result.bytes_per_glyph,
                "total_bytes": result.total_bytes,
                "ascii_half_width": result.ascii_half_width,
                "font": result.font_name,
                "layout": result.layout.human,
                "array_name": result.array_name,
                "data_crc32": result.data_crc32,
                "data_sha256": result.data_sha256,
                "note": "c_code 已写入文件；如需预览内容请调用 font.extract",
            },
            evidence_ids=[f"source:{relative}"],
        )


class DisplayVerifyTool:
    input_model = _DisplayVerifyArguments
    descriptor = AgentToolDescriptor(
        name="display.verify",
        description=(
            "设备侧显示自检：解析工程内 LUXAR 生成的字模头文件，按给定多行布局"
            "重建页寻址预期帧，并与设备回传的 CRC32 对比，验证驱动把正确的字模"
            "字节写到了正确的显存位置（逻辑层验证，与模型自报无关）。"
            "header_path=工程内字模头文件（.h，须为 font.export 生成）；"
            "lines=[{text,x,y},...]=实际绘制的每行字符串与起点（y 须为 8 的"
            "倍数，多行按同一帧合并）；screen_width/screen_height=屏幕尺寸"
            "（默认 128x64）；controller=目标控制器名（如 sh1106）时，按芯片"
            "规格校验字模打包布局与控制器硬件约定是否一致——冲突直接报错"
            "（display_layout_mismatch），把位序/行序约定错误在自检阶段暴露，"
            "否则真机必然乱码（CRC 自检证明不了约定一致）；规格 verified != "
            "true 时输出 spec_verified=unverified/candidate 提示位序未经真机"
            "验证。actual_crc32=设备经 UART 打印的 FONT_CHECK 行中的 8 位"
            "十六进制 CRC，actual_sha256 可选。返回 match、预期/实际 CRC、"
            "每字形 crc32 锚点与头文件数据完整性。只读，不写文件"
        ),
        input_schema=_DisplayVerifyArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def validate_policy(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ContinuousAgentFailure | None:
        assert isinstance(arguments, _DisplayVerifyArguments)
        target = _resolve_export_path(arguments.header_path, context)
        if target is None:
            return ContinuousAgentFailure(
                category="policy",
                code="font_header_outside_project",
                message="字模头文件必须位于当前工程内，且扩展名为 .h/.hpp/.c/.cpp",
                retryable=False,
                details={"header_path": arguments.header_path},
            )
        return None

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _DisplayVerifyArguments)
        target = _resolve_export_path(arguments.header_path, context)
        if target is None:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="policy",
                    code="font_header_outside_project",
                    message="字模头文件必须位于当前工程内，且扩展名为 .h/.hpp/.c/.cpp",
                    retryable=False,
                    details={"header_path": arguments.header_path},
                ),
            )
        try:
            result = verify_display_selfcheck(
                header_path=target,
                lines=[
                    (line.text, line.x, line.y) for line in arguments.lines
                ],
                screen_width=arguments.screen_width,
                screen_height=arguments.screen_height,
                actual_crc32=arguments.actual_crc32,
                actual_sha256=arguments.actual_sha256,
                controller=arguments.controller,
            )
        except FontBitmapError as error:
            return _font_failure(error)
        except Exception:
            return ToolResult(
                success=False,
                failure=ContinuousAgentFailure(
                    category="tool",
                    code="display_verify_failed",
                    message="显示自检重建失败，请检查字模头文件与参数",
                    retryable=True,
                ),
            )
        return ToolResult(
            success=result.match,
            output={
                "match": result.match,
                "expected_crc32": result.expected_crc32,
                "expected_sha256": result.expected_sha256,
                "actual_crc32": result.actual_crc32,
                "actual_sha256": result.actual_sha256,
                "header_data_integrity": result.header_data_integrity,
                "lines": [
                    {"text": text, "x": x, "y": y}
                    for text, x, y in result.lines
                ],
                "screen_width": result.screen_width,
                "screen_height": result.screen_height,
                "frame_bytes": result.frame_bytes,
                "array_name": result.array_name,
                "glyph_crc32s": result.glyph_crc32s,
                "controller": arguments.controller,
                "layout_consistent": result.layout_consistent,
                "header_layout": (
                    {
                        "scan": result.header_layout.scan,
                        "bit_order": result.header_layout.bit_order,
                        "invert": result.header_layout.invert,
                    }
                    if result.header_layout is not None
                    else None
                ),
                "spec_verified": result.spec_verified,
                "message": (
                    "设备回传 CRC 与预期帧一致，显示逻辑层验证通过"
                    if result.match
                    else "设备回传 CRC 与预期帧不一致，请检查绘制坐标/字模文件/固件字节布局"
                ),
            },
            evidence_ids=[
                f"display.verify:{result.array_name}:{result.expected_crc32}"
            ],
            failure=(
                None
                if result.match
                else ContinuousAgentFailure(
                    category="tool",
                    code="display_selfcheck_mismatch",
                    message="显示自检不一致：字模字节或绘制逻辑与预期不符",
                    retryable=True,
                )
            ),
        )


class DisplaySelfcheckTemplateTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="display.selfcheck_template",
        description=(
            "输出设备侧显示自检参考实现（display_selfcheck.h/.c 两个文件的文本）："
            "零依赖 zlib CRC32 与 FONT_CHECK <name> <crc32> 打印函数，配合 "
            "display.verify 使用。模型把这两个文件写入工程并接入显存刷新路径："
            "清屏后绘制待验文本，再调用 display_selfcheck_report 打印 CRC，"
            "上位机用 display.verify 对比。只读，不写文件"
        ),
        input_schema=_NoArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments, context
        return ToolResult(
            success=True,
            output={
                "files": list(DISPLAY_SELFCHECK_TEMPLATES),
                **{
                    filename: content
                    for filename, content in DISPLAY_SELFCHECK_TEMPLATES.items()
                },
            },
            evidence_ids=["display.selfcheck_template"],
        )


def _chip_spec_failure(error: Exception, prefix: str = "chip_spec") -> ToolResult:
    code = getattr(error, "code", None)
    retryable = code in {"spec_unreadable"}
    return ToolResult(
        success=False,
        failure=ContinuousAgentFailure(
            category="tool" if retryable else "validation",
            code=f"{prefix}_{code or 'error'}",
            message=str(error),
            retryable=retryable,
            details=getattr(error, "details", None) or {},
        ),
    )


class ChipSpecDraftTool:
    input_model = _ChipSpecDraftArguments
    descriptor = AgentToolDescriptor(
        name="chip.spec.draft",
        description=(
            "新硬件工作流第 1 步：把从 PDF/库源码提取的芯片事实起草为一份芯片规格"
            "（verified=unverified，无验证记录）并写入内置规格目录 src/luxar/specs/"
            "chips/<controller>.yaml，此后 font.extract/font.export 的 controller 即"
            "可用该芯片——无需改引擎代码。**仅用于新芯片**：控制器已有规格时本工具"
            "拒绝执行（防止覆盖已验证数据），验证/固化请用 chip.spec.verify。"
            "layout 与 screen 为必需参数；facts 应包含"
            "vendor/interfaces/resolutions/memory_layout/sources（来源分级 library|"
            "pdf|approximation，输入优先级：主流库源码 > PDF > 近似芯片）。"
            "写完立即生效（引擎缓存自动刷新）。写公共规格目录需要用户批准"
        ),
        input_schema=_ChipSpecDraftArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del context
        assert isinstance(arguments, _ChipSpecDraftArguments)
        from luxar.specs import (
            clear_chip_cache,
            draft_chip_spec,
            find_chip_skill,
            write_chip_skill,
        )

        try:
            existing = find_chip_skill(arguments.controller)
            if existing is not None:
                raise ValueError(
                    f"控制器 {arguments.controller!r} 已有规格（verified="
                    f"{existing.verified}，{len(existing.verification)} 条验证记录）。"
                    "chip.spec.draft 只用于新芯片起草，禁止覆盖已有规格——"
                    "验证/固化请用 chip.spec.verify"
                )
            skill = draft_chip_spec(
                controller=arguments.controller,
                aliases=arguments.aliases,
                family=arguments.family,
                facts=arguments.facts,
                spec={
                    "init": arguments.init,
                    "driver_template": arguments.driver_template,
                },
                display={
                    "layout": arguments.layout,
                    "screen": arguments.screen,
                    "column_offset": arguments.column_offset,
                    "display_options": arguments.display_options,
                },
                diagnostics=arguments.diagnostics,
            )
            path = write_chip_skill(skill)
            clear_chip_cache()
        except Exception as error:
            return _chip_spec_failure(error, prefix="chip_spec_draft")
        return ToolResult(
            success=True,
            output={
                "controller": skill.controller,
                "verified": skill.verified,
                "path": str(path),
                "spec": skill.spec.model_dump(mode="json"),
                "facts": skill.facts.model_dump(mode="json"),
                "note": (
                    "规格已接入：font.extract 可直接用 controller=该芯片。"
                    "接下来进入分层验证：构建（L1）→ display.verify（L2）→ "
                    "判别图案真机（L3）→ 用户视觉确认（L4），每步用 "
                    "chip.spec.verify 记录；≥2 次不同任务的真机成功才固化"
                ),
            },
            evidence_ids=[f"chip_spec:{skill.controller}:draft"],
        )


class ChipSpecVerifyTool:
    input_model = _ChipSpecVerifyArguments
    descriptor = AgentToolDescriptor(
        name="chip.spec.verify",
        description=(
            "新硬件工作流第 2 步：给已有芯片规格追加一条分层验证记录并写回（只追加"
            "不改历史）。level=L1(静态构建)/L2(display.verify CRC)/L3(判别图案真机)"
            "/L4(用户视觉确认)。注意：只有 L3/L4 的 pass 记录参与固化——CRC 自检"
            "证明不了位序/行序约定。verified 状态机：≥1 次 L3/L4 pass → candidate，"
            "≥2 次**不同 task** 的 L3/L4 pass → true（同一任务重复烧录不算第二次）。"
            "L4 pass 必须 user_confirmed=true。记录追加后规格文件自动写回并刷新缓存。"
            "写公共规格目录需要用户批准"
        ),
        input_schema=_ChipSpecVerifyArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del context
        assert isinstance(arguments, _ChipSpecVerifyArguments)
        from luxar.specs import (
            append_verification,
            clear_chip_cache,
            find_chip_skill,
            write_chip_skill,
        )

        try:
            skill = find_chip_skill(arguments.controller)
            if skill is None:
                raise ValueError(
                    f"未知控制器 {arguments.controller!r}：请先用 chip.spec.draft "
                    "起草规格"
                )
            entry = {
                "level": arguments.level,
                "task": arguments.task,
                "date": arguments.date,
                "project": arguments.project,
                "pattern": arguments.pattern,
                "result": arguments.result,
                "user_confirmed": arguments.user_confirmed,
                "evidence": arguments.evidence,
                "hardware": arguments.hardware,
                "expected_crc": arguments.expected_crc,
                "actual_crc": arguments.actual_crc,
            }
            updated = append_verification(skill, entry)
            path = write_chip_skill(updated)
            clear_chip_cache()
        except Exception as error:
            return _chip_spec_failure(error, prefix="chip_spec_verify")
        return ToolResult(
            success=True,
            output={
                "controller": updated.controller,
                "verified": updated.verified,
                "path": str(path),
                "verification_count": len(updated.verification),
                "entries": [e.model_dump(mode="json") for e in updated.verification],
                "note": (
                    "已记录。固化判定：不同 task 的 L3/L4 pass 数量 "
                    f"= {len({e.task for e in updated.verification if e.level in ('L3','L4') and e.result == 'pass'})}/2"
                ),
            },
            evidence_ids=[f"chip_spec:{updated.controller}:verify"],
        )


class EspIdfBuildTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="espidf.build",
        description="构建当前 ESP-IDF 工程并返回真实构建证据",
        input_schema=_NoArguments.model_json_schema(),
        read_only=False,
        requires_approval=False,
    )

    def __init__(self, builder: EspIdfPort) -> None:
        self._builder = builder

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments
        evidence = self._builder.build(_project_path(context))
        return ToolResult(
            success=evidence.success,
            output=evidence.model_dump(mode="json"),
            evidence_ids=["build:latest"] if evidence.success else [],
            failure=(
                None
                if evidence.success
                else ContinuousAgentFailure(
                    category="tool",
                    code=evidence.error_category or "build_failed",
                    message="ESP-IDF 构建未通过",
                    retryable=True,
                )
            ),
        )


class DeviceDiscoverTool:
    input_model = _NoArguments
    descriptor = AgentToolDescriptor(
        name="device.discover",
        description="发现当前主机上可安全使用的开发板串口",
        input_schema=_NoArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(self, flasher: EspIdfFlashPort) -> None:
        self._flasher = flasher

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        del arguments, context
        ports = self._flasher.discover_serial_ports()
        return ToolResult(
            success=True,
            output={
                "ports": [item.model_dump(mode="json") for item in ports],
                "count": len(ports),
            },
        )


class DeviceFlashTool:
    input_model = _FlashArguments
    descriptor = AgentToolDescriptor(
        name="device.flash",
        description="把当前工程固件烧录到用户选择的串口",
        input_schema=_FlashArguments.model_json_schema(),
        read_only=False,
        requires_approval=True,
    )

    def __init__(self, flasher: EspIdfFlashPort) -> None:
        self._flasher = flasher

    def validate_policy(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ContinuousAgentFailure | None:
        del context
        assert isinstance(arguments, _FlashArguments)
        ports = self._flasher.discover_serial_ports()
        if arguments.serial_port not in {item.name for item in ports}:
            return ContinuousAgentFailure(
                category="policy",
                code="serial_port_not_discovered",
                message="目标串口不在当前安全发现列表中",
                retryable=True,
                details={"serial_port": arguments.serial_port},
            )
        return None

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _FlashArguments)
        evidence = self._flasher.flash(
            _project_path(context),
            arguments.serial_port,
        )
        return ToolResult(
            success=evidence.success,
            output=evidence.model_dump(mode="json"),
            evidence_ids=["flash:latest"] if evidence.success else [],
            failure=(
                None
                if evidence.success
                else ContinuousAgentFailure(
                    category="tool",
                    code=evidence.error_category or "flash_failed",
                    message="设备烧录未通过",
                    retryable=True,
                )
            ),
        )


class DeviceMonitorTool:
    input_model = _MonitorArguments
    descriptor = AgentToolDescriptor(
        name="device.monitor",
        description="在受控时间内读取开发板串口日志并提取诊断",
        input_schema=_MonitorArguments.model_json_schema(),
        read_only=True,
        requires_approval=False,
    )

    def __init__(
        self,
        monitor: EspIdfMonitorPort,
        *,
        port_discoverer: EspIdfFlashPort | None = None,
    ) -> None:
        self._monitor = monitor
        self._port_discoverer = port_discoverer

    def validate_policy(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ContinuousAgentFailure | None:
        del context
        assert isinstance(arguments, _MonitorArguments)
        if self._port_discoverer is None:
            return None
        ports = self._port_discoverer.discover_serial_ports()
        if arguments.serial_port not in {item.name for item in ports}:
            return ContinuousAgentFailure(
                category="policy",
                code="serial_port_not_discovered",
                message="目标串口不在当前安全发现列表中",
                retryable=True,
                details={"serial_port": arguments.serial_port},
            )
        return None

    def execute(
        self,
        arguments: BaseModel,
        context: AgentToolExecutionContext,
    ) -> ToolResult:
        assert isinstance(arguments, _MonitorArguments)
        evidence = self._monitor.monitor(
            _project_path(context),
            arguments.serial_port,
            arguments.timeout_seconds,
        )
        return ToolResult(
            success=True,
            output=evidence.model_dump(mode="json"),
            evidence_ids=["monitor:latest"],
        )


def create_core_tool_registry(
    *,
    workspace: WorkspacePort | None = None,
    code_executor: CodeExecutorPort | None = None,
    knowledge: KnowledgeSearchPort | None = None,
    knowledge_writer: KnowledgeWritePort | None = None,
    driver_library: DriverLibraryPort | None = None,
    pdf_reader: object | None = None,
    analyzer: ProjectAnalyzer | None = None,
    builder: EspIdfPort | None = None,
    flasher: EspIdfFlashPort | None = None,
    monitor: EspIdfMonitorPort | None = None,
    persistence: PersistencePort | None = None,
    ledger: ToolExecutionLedgerPort | None = None,
    target_chip: str | None = None,
) -> ToolRegistry:
    tools = []
    if workspace is not None:
        tools.extend(
            [
                WorkspaceReadProjectTool(workspace),
                ProjectInspectTool(
                    workspace=workspace,
                    analyzer=analyzer,
                    persistence=persistence,
                    target_chip=target_chip,
                ),
            ]
        )
    if code_executor is not None:
        tools.append(WorkspaceApplyChangeBundleTool(code_executor))
    if knowledge is not None:
        tools.append(KnowledgeSearchTool(knowledge))
        tools.append(KnowledgeEntityCandidatesTool(knowledge))
        tools.append(KnowledgeEntityRegisterTool(knowledge))
    if pdf_reader is not None:
        tools.append(PdfReadTool(pdf_reader))
    if knowledge_writer is not None:
        tools.append(
            KnowledgeImportPdfTool(knowledge_writer, reader=pdf_reader)
        )
    if driver_library is not None:
        tools.extend(
            [
                DriverSearchTool(driver_library),
                DriverReadTool(driver_library),
                DriverPublishTool(driver_library, persistence),
            ]
        )
    if builder is not None:
        tools.append(EspIdfBuildTool(builder))
    if flasher is not None:
        tools.extend([DeviceDiscoverTool(flasher), DeviceFlashTool(flasher)])
    if monitor is not None:
        tools.append(
            DeviceMonitorTool(monitor, port_discoverer=flasher)
        )
    tools.extend(
        [
            FontExtractTool(),
            FontExportTool(),
            DisplayVerifyTool(),
            DisplaySelfcheckTemplateTool(),
            ChipSpecDraftTool(),
            ChipSpecVerifyTool(),
            DriverVerifyTool(),
        ]
    )
    return ToolRegistry(tools, ledger=ledger)


__all__ = [
    "ChipSpecDraftTool",
    "ChipSpecVerifyTool",
    "DisplaySelfcheckTemplateTool",
    "DisplayVerifyTool",
    "DriverPublishTool",
    "DriverReadTool",
    "DriverSearchTool",
    "DeviceDiscoverTool",
    "DeviceFlashTool",
    "DeviceMonitorTool",
    "DriverVerifyTool",
    "EspIdfBuildTool",
    "FontExportTool",
    "FontExtractTool",
    "KnowledgeEntityCandidatesTool",
    "KnowledgeEntityRegisterTool",
    "KnowledgeImportPdfTool",
    "KnowledgeSearchTool",
    "PdfReadTool",
    "ProjectInspectTool",
    "WorkspaceApplyChangeBundleTool",
    "WorkspaceReadProjectTool",
    "create_core_tool_registry",
]
