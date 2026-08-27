from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver

from luxar.adapters.fake_project_analyzer import FakeProjectAnalyzer
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.application.specialized_context import SpecializedRuntimeContext
from luxar.application.specialized_graph import build_specialized_graph
from luxar.application.specialized_results import (
    specialized_state_to_result,
    specialized_user_message,
)
from luxar.application.specialized_runner import (
    resume_specialized_workflow,
    run_specialized_workflow,
)
from luxar.application.specialized_state import SpecializedWorkflowState
from luxar.database import TransientPersistence
from luxar.database.persistence import KnowledgeMatch
from luxar.domain.knowledge_tasks import KnowledgeTask
from luxar.domain.knowledge_answers import GroundedKnowledgeAnswer
from luxar.domain.project_analysis import ProjectAnalysis, ProjectEvidenceDecision
from luxar.domain.repairs import ProjectFile
from luxar.document_reader import PdfBatch, PdfReadProgress


class FixedKnowledgeParser:
    def __init__(self, task: KnowledgeTask) -> None:
        self.task = task
        self.calls: list[str] = []

    def parse(self, task_text: str) -> KnowledgeTask:
        self.calls.append(task_text)
        return self.task


class RecordingKnowledgeService:
    def __init__(self) -> None:
        self.ingested: list[dict[str, object]] = []

    def ingest(self, **kwargs: object) -> object:
        self.ingested.append(kwargs)
        return SimpleNamespace(document_id="doc-1", chunks=2)


class SearchKnowledgeService(RecordingKnowledgeService):
    def search(self, **kwargs: object) -> list[KnowledgeMatch]:
        del kwargs
        return [
            KnowledgeMatch(
                document_id="esp32-datasheet",
                title="ESP32 数据手册",
                source_uri="docs/esp32.pdf",
                ordinal=0,
                content="GPIO34 只能作为输入使用，不支持输出。",
                score=0.98,
                knowledge_id="ka-gpio34",
                subject="GPIO34",
                category="pin",
                source_pages=(12,),
                limitations=("不能配置为输出",),
            ),
            KnowledgeMatch(
                document_id="esp32-datasheet",
                title="ESP32 数据手册",
                source_uri="docs/esp32.pdf",
                ordinal=1,
                content="GPIO0 是启动绑带引脚，上电复位时的电平会影响启动模式。",
                score=0.96,
                knowledge_id="ka-gpio0",
                subject="GPIO0",
                category="boot",
                source_pages=(15,),
            ),
            KnowledgeMatch(
                document_id="esp32-datasheet",
                title="ESP32 数据手册",
                source_uri="docs/esp32.pdf",
                ordinal=2,
                content="Wi-Fi 工作期间 ADC2 可能被无线驱动占用。",
                score=0.94,
                knowledge_id="ka-adc2",
                subject="ADC2",
                category="analog",
                source_pages=(28,),
            ),
        ]


def _context(
    *,
    parser: FixedKnowledgeParser | None = None,
    service: object | None = None,
    persistence: TransientPersistence | None = None,
    answerer: object | None = None,
    document_reader: object | None = None,
    project_analysis: ProjectAnalysis | None = None,
) -> SpecializedRuntimeContext:
    analysis = project_analysis or ProjectAnalysis(
        project_exists=True,
        has_source_code=True,
        fingerprint="placeholder",
        summary="独立分析得到的最小应用",
        entry_points=["main/main.c"],
        evidence_paths=["main/main.c"],
    )
    return SpecializedRuntimeContext(
        project_path=Path("workspace/blink"),
        workspace=FakeWorkspace(
            [
                ProjectFile(
                    path="CMakeLists.txt",
                    content="project(blink)",
                ),
                ProjectFile(
                    path="main/main.c",
                    content="void app_main(void) {}",
                ),
            ]
        ),
        checkpointer=InMemorySaver(),
        target_chip="esp32",
        project_analyzer=FakeProjectAnalyzer([analysis]),
        persistence=persistence,
        project_key="0:blink",
        knowledge_task_parser=parser,
        knowledge_service=service,  # type: ignore[arg-type]
        knowledge_answerer=answerer,  # type: ignore[arg-type]
        document_reader=document_reader,  # type: ignore[arg-type]
    )


class ProgressPdfReader:
    def iter_batches(self, path: Path, *, progress_reporter=None):
        del path
        if progress_reporter is not None:
            progress_reporter(PdfReadProgress(
                "opening", 0, 2, None, 0, "PDF 已打开，共 2 页"
            ))
            progress_reporter(PdfReadProgress(
                "extracting", 1, 2, 1, 1, "已读取 1/2 页"
            ))
        yield PdfBatch(1, 1, 2, "第 1 页", True)
        if progress_reporter is not None:
            progress_reporter(PdfReadProgress(
                "extracting", 2, 2, 2, 2, "已读取 2/2 页"
            ))
            progress_reporter(PdfReadProgress(
                "extracted", 2, 2, 2, 2, "PDF 页面读取完成，共 2 页"
            ))
        yield PdfBatch(2, 2, 2, "第 2 页", False)


def test_specialized_pdf_workflow_reports_page_level_progress(
    tmp_path: Path,
) -> None:
    pdf_path = (tmp_path / "manual.pdf").resolve()
    parser = FixedKnowledgeParser(KnowledgeTask(
        action="read_pdf",
        summary="读取手册",
        file_path=str(pdf_path),
    ))
    progress = []

    result = run_specialized_workflow(
        initial_state=SpecializedWorkflowState(
            task_text=f'读取 "{pdf_path}"',
            task_mode="knowledge",
            trace=[],
        ),
        context=_context(
            parser=parser,
            document_reader=ProgressPdfReader(),
        ),
        progress_reporter=progress.append,
    )

    pdf_events = [
        item for item in progress
        if getattr(item, "progress_type", None) == "pdf"
    ]
    assert result.state["status"] == "completed"
    assert [item.current for item in pdf_events] == [0, 1, 2, 2, 2]
    assert all(item.total == 2 for item in pdf_events)
    assert pdf_events[-1].phase == "completed"


def test_specialized_graph_has_no_firmware_execution_nodes() -> None:
    nodes = set(build_specialized_graph().get_graph().nodes)

    assert {"analyze_project", "report_project"}.issubset(nodes)
    assert {
        "analyze_knowledge_task",
        "review_knowledge_task",
        "route_knowledge_action",
        "retrieve_knowledge_evidence",
        "normalize_knowledge_evidence",
        "assess_evidence_sufficiency",
        "synthesize_grounded_answer",
        "verify_grounded_answer",
        "execute_knowledge_task",
    }.issubset(nodes)
    assert not nodes.intersection(
        {
            "analyze_requirement",
            "create_plan",
            "implement_change",
            "build_project",
            "flash_project",
            "monitor_project",
            "repair_project",
        }
    )


def test_project_inspection_runs_without_legacy_workflow_state() -> None:
    progress = []
    result = run_specialized_workflow(
        initial_state=SpecializedWorkflowState(
            task_text="检查当前项目",
            task_mode="inspection",
            trace=[],
        ),
        context=_context(),
        progress_reporter=progress.append,
    )

    assert result.state["status"] == "completed"
    assert result.state["trace"] == ["analyze_project", "report_project"]
    assert "独立分析得到的最小应用" in specialized_user_message(result.state)
    assert "证据范围：本次未检索知识库" in specialized_user_message(result.state)
    assert specialized_state_to_result(result.state)["exit_code"] == 0
    assert [item.message for item in progress] == [
        "当前项目代码分析完成",
        "项目检查完成",
    ]
    assert "workspace.read_project_files" in progress[0].narrative
    assert all(item.narrative for item in progress)


def test_project_inspection_retrieves_knowledge_only_when_model_selects_it() -> None:
    analysis = ProjectAnalysis(
        project_exists=True,
        has_source_code=True,
        fingerprint="placeholder",
        summary="代码中尝试把 GPIO34 配置为输出",
        evidence_paths=["main/main.c"],
        evidence_decision=ProjectEvidenceDecision(
            code_evidence_sufficient=False,
            confirmed_from_code=["app_main 将 GPIO34 配置为输出"],
            missing_evidence=["GPIO34 的芯片级输入输出限制"],
            knowledge_retrieval="retrieve",
            knowledge_query="ESP32 GPIO34 input only output limitation",
            reason="需要芯片手册补充代码之外的引脚限制",
        ),
    )
    result = run_specialized_workflow(
        initial_state=SpecializedWorkflowState(
            task_text="检查 GPIO34 为什么不能作为输出",
            task_mode="inspection",
            response_plan={
                "operation": "workflow",
                "scope": "focused",
                "answer_budget": 600,
            },
            trace=[],
        ),
        context=_context(
            service=SearchKnowledgeService(),
            project_analysis=analysis,
        ),
    )

    assert result.state["status"] == "completed"
    assert "prepare_project_knowledge_retrieval" in result.state["trace"]
    assert "retrieve_knowledge_evidence" in result.state["trace"]
    evidence = result.state["knowledge_evidence"]
    assert any(item["source_uri"] == "project://current-source" for item in evidence)
    assert any(item["source_uri"] == "docs/esp32.pdf" for item in evidence)
    message = specialized_user_message(result.state)
    assert "检索说明：源码分析后决定检索项目知识库" in message
    assert "当前代码与检索证据" in message


def test_project_inspection_downgrades_model_retrieval_when_kb_is_unavailable() -> None:
    analysis = ProjectAnalysis(
        project_exists=True,
        has_source_code=True,
        fingerprint="placeholder",
        summary="app_main 检查结果",
        evidence_paths=["main/main.c"],
        evidence_decision=ProjectEvidenceDecision(
            code_evidence_sufficient=False,
            missing_evidence=["相关项目设计文档"],
            knowledge_retrieval="retrieve",
            knowledge_query="app_main project design",
            reason="需要项目设计文档",
        ),
    )
    result = run_specialized_workflow(
        initial_state=SpecializedWorkflowState(
            task_text="检查 app_main",
            task_mode="inspection",
            response_plan={
                "operation": "workflow",
            },
            trace=[],
        ),
        context=_context(project_analysis=analysis),
    )

    assert result.state["status"] == "completed"
    assert result.state["knowledge_retrieval_selected"] is False
    assert "retrieve_knowledge_evidence" not in result.state["trace"]
    assert result.state["knowledge_retrieval_reason"] == (
        "项目知识库当前不可用，已限定为代码检查"
    )


def test_mutating_knowledge_task_pauses_and_resumes_on_own_checkpoint() -> None:
    parser = FixedKnowledgeParser(
        KnowledgeTask(
            action="upsert",
            summary="保存器件笔记",
            source_uri="user://note",
            title="OLED 笔记",
            content="SSD1306 使用 I2C 地址 0x3C",
        )
    )
    service = RecordingKnowledgeService()
    persistence = TransientPersistence()
    context = _context(
        parser=parser,
        service=service,
        persistence=persistence,
    )

    paused = run_specialized_workflow(
        initial_state=SpecializedWorkflowState(
            task_text="保存 OLED 笔记",
            task_mode="knowledge",
            trace=[],
        ),
        context=context,
        thread_id="specialized-knowledge",
    )

    assert paused.pending_approval is not None
    assert paused.pending_approval.kind == "knowledge_write"
    assert service.ingested == []
    paused_snapshot = persistence.get_workbench_snapshot("0:blink")
    assert paused_snapshot is not None
    assert paused_snapshot.workflow_family == "knowledge_task"
    assert paused_snapshot.snapshot["status"] == "awaiting_user"
    assert paused_snapshot.snapshot["current_task_id"] == (
        "review_knowledge_task"
    )
    assert paused_snapshot.snapshot["knowledge_task"]["action"] == "upsert"

    completed = resume_specialized_workflow(
        thread_id=paused.thread_id,
        context=context,
        approved=True,
    )

    assert completed.state["status"] == "completed"
    assert completed.state["trace"] == [
        "analyze_knowledge_task",
        "review_knowledge_task",
        "route_knowledge_action",
        "execute_knowledge_task",
        "completed",
    ]
    assert len(service.ingested) == 1
    assert completed.state["knowledge_result"] == {
        "document_id": "doc-1",
        "chunks": 2,
    }
    completed_snapshot = persistence.get_workbench_snapshot("0:blink")
    assert completed_snapshot is not None
    assert completed_snapshot.snapshot["status"] == "completed"
    assert completed_snapshot.snapshot["acceptance_passed"] is True
    assert completed_snapshot.snapshot["knowledge_result"] == {
        "document_id": "doc-1",
        "chunks": 2,
    }


def test_missing_knowledge_service_becomes_sanitized_failure() -> None:
    parser = FixedKnowledgeParser(
        KnowledgeTask(action="list", summary="列出项目知识")
    )
    result = run_specialized_workflow(
        initial_state=SpecializedWorkflowState(
            task_text="列出知识",
            task_mode="knowledge",
            trace=[],
        ),
        context=_context(parser=parser),
    )

    assert result.state["status"] == "failed"
    assert result.state["error"].category == "service"
    assert "知识或文档服务暂时不可用" in specialized_user_message(
        result.state
    )


def test_search_generates_and_verifies_a_direct_grounded_answer() -> None:
    parser = FixedKnowledgeParser(
        KnowledgeTask(
            action="search",
            summary="回答 ESP32 引脚问题",
            query="ESP32 各引脚功能",
        )
    )
    result = run_specialized_workflow(
        initial_state=SpecializedWorkflowState(
            task_text="现在告诉我 ESP32 的各引脚功能",
            task_mode="knowledge",
            trace=[],
        ),
        context=_context(parser=parser, service=SearchKnowledgeService()),
    )

    assert result.state["status"] == "completed"
    assert result.state["answer_verification"]["passed"] is True
    assert result.state["knowledge_result"]["evidence_count"] == 3
    message = specialized_user_message(result.state)
    assert "GPIO34" in message
    assert "[E1]" in message
    assert "共找到" not in message
    assert result.state["trace"] == [
        "analyze_knowledge_task",
        "review_knowledge_task",
        "route_knowledge_action",
        "retrieve_knowledge_evidence",
        "normalize_knowledge_evidence",
        "assess_evidence_sufficiency",
        "synthesize_grounded_answer",
        "verify_grounded_answer",
        "completed",
    ]


def test_search_revises_a_non_answer_before_completing() -> None:
    class RevisingAnswerer:
        def __init__(self) -> None:
            self.calls = 0

        def answer(self, **kwargs: object) -> GroundedKnowledgeAnswer:
            self.calls += 1
            if self.calls == 1:
                return GroundedKnowledgeAnswer(
                    answer_markdown="检索完成，来源是 ESP32 数据手册。[E1]",
                    cited_evidence_ids=["E1"],
                )
            assert kwargs["revision_instructions"]
            return GroundedKnowledgeAnswer(
                answer_markdown="GPIO34 只能作为输入使用，不能配置为输出。[E1]",
                cited_evidence_ids=["E1"],
            )

    parser = FixedKnowledgeParser(
        KnowledgeTask(action="search", summary="查询 GPIO34", query="GPIO34")
    )
    answerer = RevisingAnswerer()
    result = run_specialized_workflow(
        initial_state=SpecializedWorkflowState(
            task_text="GPIO34 有什么限制？",
            task_mode="knowledge",
            trace=[],
        ),
        context=_context(
            parser=parser,
            service=SearchKnowledgeService(),
            answerer=answerer,
        ),
    )

    assert result.state["status"] == "completed"
    assert answerer.calls == 2
    assert "revise_grounded_answer" in result.state["trace"]
    assert result.state["answer_revision_count"] == 1


def test_search_without_evidence_never_reports_completed() -> None:
    class EmptyKnowledgeService(RecordingKnowledgeService):
        def search(self, **kwargs: object) -> list[KnowledgeMatch]:
            del kwargs
            return []

    parser = FixedKnowledgeParser(
        KnowledgeTask(action="search", summary="查询未知器件", query="UNKNOWN-X")
    )
    result = run_specialized_workflow(
        initial_state=SpecializedWorkflowState(
            task_text="告诉我 UNKNOWN-X 的全部引脚",
            task_mode="knowledge",
            trace=[],
        ),
        context=_context(parser=parser, service=EmptyKnowledgeService()),
    )

    assert result.state["status"] == "failed"
    assert result.state["error"].category == "knowledge_insufficient"
    assert result.state.get("knowledge_result") is None
    assert result.state["trace"].count("retrieve_knowledge_evidence") == 2
    assert "expand_knowledge_query" in result.state["trace"]
