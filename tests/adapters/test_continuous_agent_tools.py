from __future__ import annotations

from pathlib import Path

from luxar.adapters.continuous_agent_tools import create_core_tool_registry
from luxar.database.persistence import KnowledgeMatch
from luxar.document_reader import PdfBatch
from luxar.domain.agent.code_changes import ChangeBundle, ChangeBundleValidation
from luxar.domain.continuous_agent.steps import ToolCall
from luxar.domain.devices import FlashEvidence, MonitorEvidence, SerialPortInfo
from luxar.domain.evidence import BuildEvidence
from luxar.domain.repairs import ProjectFile
from luxar.knowledge import IngestedDocument, IngestedPdf
from luxar.ports.agent_tool import AgentToolExecutionContext


class _Workspace:
    def read_project_files(self, project_path: Path) -> list[ProjectFile]:
        assert project_path.name == "test4"
        return [
            ProjectFile(
                path="main/main.c",
                content="void app_main(void) {}\n",
            )
        ]


class _Builder:
    def build(self, project_path: Path) -> BuildEvidence:
        assert project_path.name == "test4"
        return BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
        )


class _Device:
    def __init__(self) -> None:
        self.flash_calls = 0
        self.monitor_calls = 0

    def discover_serial_ports(self) -> list[SerialPortInfo]:
        return [SerialPortInfo(name="COM4", description="ESP32")]

    def flash(self, project_path: Path, port: str) -> FlashEvidence:
        self.flash_calls += 1
        return FlashEvidence(
            success=True,
            command=["idf.py", "-p", port, "flash"],
            return_code=0,
            port=port,
        )

    def monitor(
        self,
        project_path: Path,
        port: str,
        timeout_seconds: int,
    ) -> MonitorEvidence:
        self.monitor_calls += 1
        return MonitorEvidence(
            command=["idf.py", "-p", port, "monitor"],
            port=port,
            capture_timeout_seconds=timeout_seconds,
            captured_log="I (10) app: ready",
            terminated_by_timeout=True,
        )


class _CodeExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        project_path: Path,
        bundle: ChangeBundle,
    ) -> ChangeBundleValidation:
        self.calls += 1
        assert project_path.name == "test4"
        return ChangeBundleValidation(
            before_fingerprint="a" * 64,
            after_fingerprint="b" * 64,
            changed_files=[bundle.changes[0].path],
            diff_summary=[f"create: {bundle.changes[0].path}"],
        )


class _Knowledge:
    def search(
        self,
        *,
        project_key: str,
        query: str,
        limit: int = 6,
    ) -> list[KnowledgeMatch]:
        assert project_key == "0:test4"
        assert query == "TWAI 终端电阻"
        assert limit == 3
        return [
            KnowledgeMatch(
                document_id="doc-1",
                title="TWAI 指南",
                source_uri="docs/twai.md",
                ordinal=2,
                content="总线两端应各有一个终端电阻。",
                score=0.91,
            )
        ]

    def entity_candidates(self) -> list[dict[str, object]]:
        return [
            {
                "scope_key": "controller",
                "scope_value": "sh1106",
                "documents": [
                    {"source_uri": "docs/sh1106.pdf", "title": "芯片手册"},
                    {"source_uri": "docs/panel.pdf", "title": "屏厂规格书"},
                ],
            }
        ]

    def register_entity(self, *, entity: object, replace: bool = False) -> bool:
        self.last_registered = entity
        return True

    def list_entities(self) -> list[object]:
        return [getattr(self, "last_registered", None)] if hasattr(self, "last_registered") else []


class _PdfReader:
    def __init__(
        self,
        content: str = "SH1106 datasheet body",
        total_pages: int = 37,
    ) -> None:
        self.content = content
        self.total_pages = total_pages

    def iter_batches(self, path: Path):
        del path
        return iter(
            [
                PdfBatch(
                    start_page=1,
                    end_page=self.total_pages,
                    total_pages=self.total_pages,
                    content=self.content,
                    has_more=False,
                    section_title="SH1106",
                )
            ]
        )


class _KnowledgeWriter:
    def __init__(self) -> None:
        self.ingest_calls = 0
        self.last_path: Path | None = None

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
    ) -> IngestedPdf:
        del reader, extractor, progress_reporter
        self.ingest_calls += 1
        self.last_path = path
        assert project_key == "0:test4"
        assert title == "SH1106 手册"
        return IngestedPdf(
            source_uri=source_uri,
            total_pages=37,
            batches=1,
            documents=[
                IngestedDocument(
                    document_id="doc-pdf-1",
                    chunks=12,
                    content_hash="hash-1",
                )
            ],
            knowledge_units=12,
        )

def _context(project_path: Path) -> AgentToolExecutionContext:
    return AgentToolExecutionContext(
        session_id="session-1",
        turn_id="turn-1",
        project_key="0:test4",
        project_path=project_path,
    )


def test_core_registry_wraps_existing_read_build_and_device_ports(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    device = _Device()
    registry = create_core_tool_registry(
        workspace=_Workspace(),  # type: ignore[arg-type]
        builder=_Builder(),  # type: ignore[arg-type]
        flasher=device,
        monitor=device,
    )

    assert [item.name for item in registry.descriptors()] == [
        "chip.spec.draft",
        "chip.spec.verify",
        "device.discover",
        "device.flash",
        "device.monitor",
        "display.selfcheck_template",
        "display.verify",
        "driver.verify",
        "espidf.build",
        "font.export",
        "font.extract",
        "project.inspect",
        "workspace.read_project",
    ]

    read = registry.dispatch(
        ToolCall(
            call_id="read",
            tool_name="workspace.read_project",
            arguments={},
        ),
        _context(project),
    )
    build = registry.dispatch(
        ToolCall(call_id="build", tool_name="espidf.build", arguments={}),
        _context(project),
    )
    discover = registry.dispatch(
        ToolCall(
            call_id="discover",
            tool_name="device.discover",
            arguments={},
        ),
        _context(project),
    )
    monitor = registry.dispatch(
        ToolCall(
            call_id="monitor",
            tool_name="device.monitor",
            arguments={"serial_port": "COM4", "timeout_seconds": 5},
        ),
        _context(project),
    )

    assert read.call.result["file_count"] == 1  # type: ignore[index]
    assert build.call.status == "succeeded"
    assert discover.call.result["ports"][0]["name"] == "COM4"  # type: ignore[index]
    assert monitor.call.result["captured_log"] == "I (10) app: ready"  # type: ignore[index]
    assert device.monitor_calls == 1


def test_flash_wrapper_requires_approval_before_touching_device(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    device = _Device()
    registry = create_core_tool_registry(flasher=device)
    call = ToolCall(
        call_id="flash",
        tool_name="device.flash",
        arguments={"serial_port": "COM4"},
    )

    pending = registry.dispatch(call, _context(project))
    assert pending.pending_approval is not None
    assert device.flash_calls == 0

    completed = registry.dispatch(call, _context(project), approved=True)
    assert completed.call.status == "succeeded"
    assert device.flash_calls == 1


def test_device_tools_reject_serial_port_outside_discovery_before_approval(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    device = _Device()
    registry = create_core_tool_registry(flasher=device, monitor=device)

    flash = registry.dispatch(
        ToolCall(
            call_id="unsafe-flash",
            tool_name="device.flash",
            arguments={"serial_port": "COM99"},
        ),
        _context(project),
    )
    monitor = registry.dispatch(
        ToolCall(
            call_id="unsafe-monitor",
            tool_name="device.monitor",
            arguments={"serial_port": "COM99", "timeout_seconds": 5},
        ),
        _context(project),
    )

    assert flash.pending_approval is None
    assert flash.call.failure is not None
    assert flash.call.failure.code == "serial_port_not_discovered"
    assert monitor.call.failure is not None
    assert monitor.call.failure.category == "policy"
    assert device.flash_calls == 0
    assert device.monitor_calls == 0


def test_build_failure_is_normalized_as_tool_failure(tmp_path: Path) -> None:
    class FailedBuilder:
        def build(self, project_path: Path) -> BuildEvidence:
            return BuildEvidence(
                success=False,
                command=["idf.py", "build"],
                return_code=2,
                error_category="source",
                stderr_summary="compile failed",
            )

    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry(builder=FailedBuilder())

    outcome = registry.dispatch(
        ToolCall(call_id="build", tool_name="espidf.build", arguments={}),
        _context(project),
    )

    assert outcome.call.status == "failed"
    assert outcome.call.failure is not None
    assert outcome.call.failure.category == "tool"
    assert outcome.call.failure.code == "source"


def test_change_bundle_requires_approval_and_uses_typed_executor(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    executor = _CodeExecutor()
    registry = create_core_tool_registry(code_executor=executor)
    call = ToolCall(
        call_id="apply-main",
        tool_name="workspace.apply_change_bundle",
        arguments={
            "bundle": {
                "bundle_id": "bundle-1",
                "task_id": "task-1",
                "description": "新增入口",
                "changes": [
                    {
                        "operation": "create",
                        "path": "main/main.c",
                        "content": "void app_main(void) {}\n",
                        "expected_sha256": None,
                    }
                ],
                "allowed_paths": ["main/**"],
                "preserves": [],
            }
        },
    )

    waiting = registry.dispatch(call, _context(project))
    completed = registry.dispatch(call, _context(project), approved=True)

    assert waiting.pending_approval is not None
    assert executor.calls == 1
    assert completed.call.status == "succeeded"
    assert completed.call.result["changed_files"] == ["main/main.c"]  # type: ignore[index]


def test_knowledge_search_is_project_scoped_and_returns_evidence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry(knowledge=_Knowledge())

    result = registry.dispatch(
        ToolCall(
            call_id="knowledge",
            tool_name="knowledge.search",
            arguments={"query": "TWAI 终端电阻", "limit": 3},
        ),
        _context(project),
    )

    assert result.call.status == "succeeded"
    assert result.call.result["count"] == 1  # type: ignore[index]
    assert result.call.result["matches"][0]["source_uri"] == "docs/twai.md"  # type: ignore[index]


def test_pdf_read_returns_content_and_metadata(tmp_path: Path) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    pdf = tmp_path / "sh1106.pdf"
    pdf.write_text("placeholder", encoding="utf-8")
    registry = create_core_tool_registry(
        pdf_reader=_PdfReader(),  # type: ignore[arg-type]
    )

    result = registry.dispatch(
        ToolCall(
            call_id="pdf-read",
            tool_name="pdf.read",
            arguments={"file_path": str(pdf)},
        ),
        _context(project),
    )

    assert result.call.status == "succeeded"
    assert result.call.result["read"] is True  # type: ignore[index]
    assert result.call.result["content"] == "SH1106 datasheet body"  # type: ignore[index]
    assert result.call.result["total_pages"] == 37  # type: ignore[index]
    assert result.call.result["truncated"] is False  # type: ignore[index]
    assert result.call.evidence_ids == [f"pdf:{pdf}"]


def test_pdf_read_truncates_content_to_requested_budget(tmp_path: Path) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    pdf = tmp_path / "sh1106.pdf"
    pdf.write_text("placeholder", encoding="utf-8")
    registry = create_core_tool_registry(
        pdf_reader=_PdfReader(content="x" * 5_000),  # type: ignore[arg-type]
    )

    result = registry.dispatch(
        ToolCall(
            call_id="pdf-read-trunc",
            tool_name="pdf.read",
            arguments={"file_path": str(pdf), "max_characters": 1_000},
        ),
        _context(project),
    )

    assert result.call.status == "succeeded"
    assert result.call.result["truncated"] is True  # type: ignore[index]
    assert len(result.call.result["content"]) == 1_000  # type: ignore[index]


def test_pdf_read_rejects_relative_path_escaping_project(tmp_path: Path) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry(
        pdf_reader=_PdfReader(),  # type: ignore[arg-type]
    )

    result = registry.dispatch(
        ToolCall(
            call_id="pdf-read-escape",
            tool_name="pdf.read",
            arguments={"file_path": "../outside.pdf"},
        ),
        _context(project),
    )

    assert result.call.status == "failed"
    assert result.call.failure is not None
    assert result.call.failure.code == "pdf_path_outside_project"


def test_knowledge_import_requires_approval_before_ingesting_pdf(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    pdf = tmp_path / "sh1106.pdf"
    pdf.write_text("placeholder", encoding="utf-8")
    writer = _KnowledgeWriter()
    registry = create_core_tool_registry(
        knowledge_writer=writer,
        pdf_reader=_PdfReader(),  # type: ignore[arg-type]
    )
    call = ToolCall(
        call_id="import-pdf",
        tool_name="knowledge.import",
        arguments={"file_path": str(pdf), "title": "SH1106 手册"},
    )

    waiting = registry.dispatch(call, _context(project))
    completed = registry.dispatch(call, _context(project), approved=True)

    assert waiting.pending_approval is not None
    assert waiting.pending_approval.tool_name == "knowledge.import"
    assert "sh1106.pdf" in waiting.pending_approval.summary
    assert writer.ingest_calls == 1
    assert writer.last_path == pdf.resolve()
    assert completed.call.status == "succeeded"
    assert completed.call.result["document_ids"] == ["doc-pdf-1"]  # type: ignore[index]
    assert completed.call.evidence_ids == ["knowledge:doc-pdf-1"]


def test_knowledge_import_reports_missing_pdf(tmp_path: Path) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    writer = _KnowledgeWriter()
    registry = create_core_tool_registry(
        knowledge_writer=writer,
        pdf_reader=_PdfReader(),  # type: ignore[arg-type]
    )

    result = registry.dispatch(
        ToolCall(
            call_id="import-missing",
            tool_name="knowledge.import",
            arguments={"file_path": str(tmp_path / "missing.pdf")},
        ),
        _context(project),
        approved=True,
    )

    assert result.call.status == "failed"
    assert result.call.failure is not None
    assert result.call.failure.code == "pdf_not_found"


# ---------------------------------------------------------------------------
# chip.spec 工具（新硬件工作流：起草 -> 验证 -> 固化）
# ---------------------------------------------------------------------------


def _chip_spec_draft_arguments(controller: str = "st7567") -> dict[str, object]:
    return {
        "controller": controller,
        "facts": {
            "vendor": "测试厂商",
            "interfaces": ["i2c", "spi"],
            "sources": [{"kind": "library", "name": "u8g2", "confidence": "high"}],
        },
        "layout": {"scan": "column", "bit_order": "lsb", "invert": False},
        "screen": {"width": 128, "height": 64},
        "column_offset": 0,
        "init": [{"cmd": 0xAE}, {"cmd": 0xAF}],
    }


def _chip_spec_draft_kwargs(controller: str = "st7567") -> dict[str, object]:
    """与 draft_chip_spec 签名对齐的 kwargs（spec/display/facts/aliases...）。"""
    args = _chip_spec_draft_arguments(controller)
    return {
        "controller": args["controller"],
        "facts": args["facts"],
        "spec": {
            "init": args["init"],
        },
        "display": {
            "layout": args["layout"],
            "screen": args["screen"],
            "column_offset": args["column_offset"],
        },
    }


def test_chip_spec_draft_writes_spec_and_makes_controller_usable(
    tmp_path: Path, monkeypatch
) -> None:
    from luxar.adapters.font_bitmap import resolve_layout
    from luxar.specs.chip_skill import _BUILTIN_DIR

    monkeypatch.setattr("luxar.specs.chip_skill._BUILTIN_DIR", tmp_path)
    from luxar.specs import clear_chip_cache

    clear_chip_cache()
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry()

    result = registry.dispatch(
        ToolCall(
            call_id="draft",
            tool_name="chip.spec.draft",
            arguments=_chip_spec_draft_arguments(),
        ),
        _context(project),
        approved=True,
    )
    assert result.call.status == "succeeded", result.call.failure
    assert result.call.result is not None
    assert result.call.result["controller"] == "st7567"  # type: ignore[index]
    assert result.call.result["verified"] == "unverified"  # type: ignore[index]
    assert (tmp_path / "st7567.yaml").is_file()

    # 起草后引擎立即可解析该控制器（YAML 即接入，无需改引擎代码）
    layout = resolve_layout("st7567", None, None, None)
    assert layout.scan == "column"
    assert layout.bit_order == "lsb"


def test_chip_spec_draft_rejects_existing_controller(
    tmp_path: Path, monkeypatch
) -> None:
    from luxar.specs import write_chip_skill, draft_chip_spec

    monkeypatch.setattr("luxar.specs.chip_skill._BUILTIN_DIR", tmp_path)
    from luxar.specs import clear_chip_cache

    clear_chip_cache()
    write_chip_skill(
        draft_chip_spec(**_chip_spec_draft_kwargs()),
        directory=tmp_path,
    )
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry()

    result = registry.dispatch(
        ToolCall(
            call_id="draft-dup",
            tool_name="chip.spec.draft",
            arguments=_chip_spec_draft_arguments(),
        ),
        _context(project),
        approved=True,
    )
    assert result.call.status == "failed"
    assert result.call.failure is not None
    assert "已有规格" in result.call.failure.message


def test_chip_spec_verify_promotes_to_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    from luxar.specs import write_chip_skill, draft_chip_spec

    monkeypatch.setattr("luxar.specs.chip_skill._BUILTIN_DIR", tmp_path)
    from luxar.specs import clear_chip_cache

    clear_chip_cache()
    write_chip_skill(
        draft_chip_spec(**_chip_spec_draft_kwargs()),
        directory=tmp_path,
    )
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry()

    result = registry.dispatch(
        ToolCall(
            call_id="verify-1",
            tool_name="chip.spec.verify",
            arguments={
                "controller": "st7567",
                "level": "L3",
                "task": "corner_diag",
                "pattern": "corners_bbox",
                "evidence": "判别图案：方块位置正确、0x80 探针在页顶",
            },
        ),
        _context(project),
        approved=True,
    )
    assert result.call.status == "succeeded", result.call.failure
    assert result.call.result is not None
    assert result.call.result["verified"] == "candidate"  # type: ignore[index]
    assert result.call.result["verification_count"] == 1  # type: ignore[index]


def test_chip_spec_verify_rejects_l4_without_user_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    from luxar.specs import write_chip_skill, draft_chip_spec

    monkeypatch.setattr("luxar.specs.chip_skill._BUILTIN_DIR", tmp_path)
    from luxar.specs import clear_chip_cache

    clear_chip_cache()
    write_chip_skill(
        draft_chip_spec(**_chip_spec_draft_kwargs()),
        directory=tmp_path,
    )
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry()

    result = registry.dispatch(
        ToolCall(
            call_id="verify-l4",
            tool_name="chip.spec.verify",
            arguments={
                "controller": "st7567",
                "level": "L4",
                "task": "visual_check",
                "result": "pass",
                "user_confirmed": False,
            },
        ),
        _context(project),
        approved=True,
    )
    assert result.call.status == "failed"
    assert result.call.failure is not None
    assert "user_confirmed" in result.call.failure.message


def test_chip_spec_verify_unknown_controller(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("luxar.specs.chip_skill._BUILTIN_DIR", tmp_path)
    from luxar.specs import clear_chip_cache

    clear_chip_cache()
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry()

    result = registry.dispatch(
        ToolCall(
            call_id="verify-unknown",
            tool_name="chip.spec.verify",
            arguments={
                "controller": "nosuchchip",
                "level": "L2",
                "task": "crc_check",
            },
        ),
        _context(project),
        approved=True,
    )
    assert result.call.status == "failed"
    assert result.call.failure is not None
    assert "chip.spec.draft" in result.call.failure.message


# ---------------------------------------------------------------------------
# knowledge.entity 工具（跨文档关联候选 + 注册）
# ---------------------------------------------------------------------------


def test_entity_candidates_tool_reports_cross_document_groups() -> None:
    project = Path("test4")
    knowledge = _Knowledge()
    registry = create_core_tool_registry(knowledge=knowledge)  # type: ignore[arg-type]
    outcome = registry.dispatch(
        ToolCall(
            call_id="entity-candidates",
            tool_name="knowledge.entity.candidates",
            arguments={},
        ),
        _context(project),
    )
    assert outcome.call.status == "succeeded"
    result = outcome.call.result
    assert result is not None
    assert result["count"] == 1
    candidate = result["candidates"][0]
    assert candidate["scope_value"] == "sh1106"
    assert len(candidate["documents"]) == 2
    # 工具描述必须引导"探测→确认→注册"，不自行合并
    descriptor = next(
        d for d in registry.descriptors() if d.name == "knowledge.entity.candidates"
    )
    assert "用户确认" in descriptor.description


def test_entity_register_tool_registers_chip_and_device() -> None:
    project = Path("test4")
    knowledge = _Knowledge()
    registry = create_core_tool_registry(knowledge=knowledge)  # type: ignore[arg-type]

    # 注册 chip
    chip = registry.dispatch(
        ToolCall(
            call_id="entity-chip",
            tool_name="knowledge.entity.register",
            arguments={
                "kind": "chip",
                "name": "SH1106",
                "source_uris": ["docs/sh1106.pdf"],
            },
        ),
        _context(project),
        approved=True,
    )
    assert chip.call.status == "succeeded"
    chip_result = chip.call.result
    assert chip_result is not None
    assert chip_result["kind"] == "chip"
    assert chip_result["entity_id"].startswith("chip-")
    chip_id = chip_result["entity_id"]

    # 注册 device 引用该 chip
    device = registry.dispatch(
        ToolCall(
            call_id="entity-device",
            tool_name="knowledge.entity.register",
            arguments={
                "kind": "device",
                "name": "1.3寸横屏",
                "chip_ref": chip_id,
                "source_uris": ["docs/panel.pdf"],
            },
        ),
        _context(project),
        approved=True,
    )
    assert device.call.status == "succeeded"
    device_result = device.call.result
    assert device_result is not None
    assert device_result["chip_ref"] == chip_id


def test_entity_register_requires_approval() -> None:
    project = Path("test4")
    knowledge = _Knowledge()
    registry = create_core_tool_registry(knowledge=knowledge)  # type: ignore[arg-type]
    call = ToolCall(
        call_id="entity-approval",
        tool_name="knowledge.entity.register",
        arguments={"kind": "chip", "name": "BMP280"},
    )
    waiting = registry.dispatch(call, _context(project))
    # 未审批：挂起等待；批准后执行
    assert waiting.pending_approval is not None
    assert waiting.pending_approval.tool_name == "knowledge.entity.register"
    completed = registry.dispatch(call, _context(project), approved=True)
    assert completed.call.status == "succeeded"


# ---------------------------------------------------------------------------
# driver.verify（引用式驱动校验）
# ---------------------------------------------------------------------------


def test_driver_verify_passes_when_all_bytes_referenced() -> None:
    project = Path("test4")
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="driver-ok",
            tool_name="driver.verify",
            arguments={
                "code": "cmd(0xAE); cmd(0xD5, 0x80); cmd(0xAF);",
                "references": [
                    {
                        "knowledge_id": "pa-1",
                        "subject": "power_up_sequence",
                        "entity_id": "device-1",
                        "excerpt": "0xAE 0xD5 0x80 0xAF",
                    }
                ],
            },
        ),
        _context(project),
    )
    assert outcome.call.status == "succeeded"
    result = outcome.call.result
    assert result is not None
    assert result["ok"] is True


def test_driver_verify_reports_invented_byte() -> None:
    """oled10 场景：模型抄了手册没有的 0x8D，校验器应报违规。"""
    project = Path("test4")
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="driver-bad",
            tool_name="driver.verify",
            arguments={
                "code": "cmd(0xAE);\ncmd(0x8D);\ncmd(0xAF);\n",
                "references": [
                    {
                        "knowledge_id": "pa-1",
                        "subject": "power_up_sequence",
                        "entity_id": "device-1",
                        "excerpt": "0xAE 0xAF",
                    }
                ],
            },
        ),
        _context(project),
    )
    assert outcome.call.status == "failed"
    assert outcome.call.failure is not None
    assert outcome.call.failure.code == "driver_byte_unreferenced"
    violations = outcome.call.failure.details["violations"]
    assert any(v["byte"] == "0x8D" for v in violations)
    assert any(v["line"] == 2 for v in violations)
