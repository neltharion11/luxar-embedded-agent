from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_firmware_editor import FakeFirmwareEditor
from luxar.adapters.fake_flasher import FakeFlasher
from luxar.adapters.fake_log_analyst import FakeLogAnalyst
from luxar.adapters.fake_monitor import FakeMonitor
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_project_analyzer import FakeProjectAnalyzer
from luxar.adapters.fake_project_creator import FakeProjectCreator
from luxar.adapters.fake_repair_planner import FakeRepairPlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.application.context import RuntimeContext
from luxar.application.project_analysis import analyze_current_project
from luxar.application.results import user_message_for_state
from luxar.application.runner import run_workflow
from luxar.application.state import WorkflowState
from luxar.database import TransientPersistence
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.project_analysis import ProjectAnalysis
from luxar.domain.idf_examples import EspIdfExampleReference
from luxar.domain.repairs import FileReplacement, ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


def analysis(summary: str) -> ProjectAnalysis:
    return ProjectAnalysis(
        project_exists=True,
        has_source_code=True,
        fingerprint="placeholder",
        summary=summary,
        entry_points=["main/main.c"],
        evidence_paths=["main/main.c"],
    )


def repair(content: str) -> RepairPlan:
    return RepairPlan(
        diagnosis="implement requested behavior",
        replacements=[FileReplacement(path="main/main.c", content=content)],
    )


def test_analysis_cache_reuses_only_an_exact_source_fingerprint() -> None:
    project_path = Path("workspace/blink")
    workspace = FakeWorkspace(
        [
            ProjectFile(path="CMakeLists.txt", content="project(blink)"),
            ProjectFile(path="main/main.c", content="void app_main(void) {}"),
        ]
    )
    analyzer = FakeProjectAnalyzer([analysis("first"), analysis("second")])
    persistence = TransientPersistence()

    first = analyze_current_project(
        project_path=project_path,
        target_chip="esp32",
        workspace=workspace,
        analyzer=analyzer,
        persistence=persistence,
        project_key="0:blink",
    )
    cached = analyze_current_project(
        project_path=project_path,
        target_chip="esp32",
        workspace=workspace,
        analyzer=analyzer,
        persistence=persistence,
        project_key="0:blink",
    )
    workspace.apply_repair(
        project_path,
        repair("void app_main(void) { int changed = 1; }"),
    )
    refreshed = analyze_current_project(
        project_path=project_path,
        target_chip="esp32",
        workspace=workspace,
        analyzer=analyzer,
        persistence=persistence,
        project_key="0:blink",
    )

    assert first.cache_hit is False
    assert cached.cache_hit is True
    assert cached.fingerprint == first.fingerprint
    assert refreshed.cache_hit is False
    assert refreshed.fingerprint != first.fingerprint
    assert len(analyzer.calls) == 2


def test_structural_gaps_are_reported_even_when_model_omits_them() -> None:
    result = analyze_current_project(
        project_path=Path("workspace/broken"),
        target_chip="esp32",
        workspace=FakeWorkspace([]),
        analyzer=FakeProjectAnalyzer([analysis("looks empty")]),
        persistence=None,
        project_key=None,
    )

    assert result.has_source_code is False
    assert any("缺少 CMakeLists.txt" in item for item in result.gaps)
    assert any("没有发现 C/C++/汇编源文件" in item for item in result.gaps)


def make_context(
    *,
    workspace: FakeWorkspace,
    analyzer: FakeProjectAnalyzer,
    planner: FakePlanner,
    editor: FakeFirmwareEditor,
    example_library: object | None = None,
) -> RuntimeContext:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )
    return RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=planner,
        espidf=FakeEspIdf(
            [BuildEvidence(success=True, command=["idf.py", "build"], return_code=0)]
        ),
        project_path=Path("workspace/blink"),
        repair_planner=FakeRepairPlanner(repair("repair fallback")),
        workspace=workspace,
        project_creator=FakeProjectCreator([]),
        target_chip="esp32",
        flasher=FakeFlasher([]),
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        serial_port=None,
        monitor_timeout_seconds=10,
        checkpointer=InMemorySaver(),
        project_analyzer=analyzer,
        firmware_editor=editor,
        example_library=example_library,  # type: ignore[arg-type]
        persistence=TransientPersistence(),
        project_key="0:blink",
    )


def test_graph_analyzes_before_planning_and_refreshes_after_implementation() -> None:
    old_source = "void app_main(void) {}"
    new_source = "void app_main(void) { gpio_set_level(2, 1); }"
    workspace = FakeWorkspace(
        [
            ProjectFile(path="CMakeLists.txt", content="project(blink)"),
            ProjectFile(path="main/main.c", content=old_source),
        ]
    )
    analyzer = FakeProjectAnalyzer(
        [analysis("empty app_main"), analysis("GPIO blink implemented")]
    )
    planner = FakePlanner(
        ExecutionPlan(
            steps=[
                PlanStep(kind="implement_change", description="implement blink"),
                PlanStep(kind="build_project", description="verify build"),
            ]
        )
    )
    editor = FakeFirmwareEditor(repair(new_source))

    class ExampleLibrary:
        def search(self, requirement, *, limit=2):
            return [
                EspIdfExampleReference(
                    path="get-started/blink",
                    score=18,
                    matched_terms=["blink", "gpio"],
                )
            ]

        def read(self, reference):
            return [
                ProjectFile(
                    path="examples/get-started/blink/main/blink.c",
                    content="gpio_set_level(2, 1);",
                )
            ]

    context = make_context(
        workspace=workspace,
        analyzer=analyzer,
        planner=planner,
        editor=editor,
        example_library=ExampleLibrary(),
    )

    result = run_workflow(
        initial_state=WorkflowState(
            task_text="blink GPIO 2",
            task_mode="firmware",
            attempts=0,
            max_attempts=2,
            trace=[],
        ),
        context=context,
    ).state

    assert result["status"] == "completed"
    assert result["trace"] == [
        "analyze_requirement",
        "analyze_project",
        "create_plan",
        "execute_next_step",
        "find_idf_examples",
        "implement_change",
        "execute_next_step",
        "build_project",
        "execute_next_step",
        "completed",
    ]
    assert planner.project_analyses[0].summary == "empty app_main"
    assert editor.calls[0][1].summary == "empty app_main"
    assert editor.calls[0][3][0].path == "get-started/blink"
    assert editor.calls[0][4][0].path.endswith("main/blink.c")
    assert result["project_analysis"].summary == "GPIO blink implemented"
    assert len(analyzer.calls) == 2
    assert workspace.files[1].content == new_source


def test_project_inspection_uses_the_shared_analysis_graph_without_planning() -> None:
    workspace = FakeWorkspace(
        [ProjectFile(path="main/main.c", content="void app_main(void) {}")]
    )
    analyzer = FakeProjectAnalyzer([analysis("minimal application")])
    planner = FakePlanner(
        ExecutionPlan(
            steps=[PlanStep(kind="build_project", description="must not run")]
        )
    )
    editor = FakeFirmwareEditor(repair("must not run"))
    context = make_context(
        workspace=workspace,
        analyzer=analyzer,
        planner=planner,
        editor=editor,
    )

    result = run_workflow(
        initial_state=WorkflowState(
            task_text="检查当前项目",
            task_mode="inspection",
            trace=[],
        ),
        context=context,
    ).state

    assert result["trace"] == ["analyze_project", "report_project"]
    assert planner.calls == []
    assert "minimal application" in user_message_for_state(result)
    assert "缺少 CMakeLists.txt" in user_message_for_state(result)
