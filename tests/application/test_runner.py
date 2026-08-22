from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_flasher import FakeFlasher
from luxar.adapters.fake_log_analyst import FakeLogAnalyst
from luxar.adapters.fake_monitor import FakeMonitor
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_project_creator import FakeProjectCreator
from luxar.adapters.fake_repair_planner import FakeRepairPlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.application.context import RuntimeContext
from luxar.application.runner import (
    WorkflowProgress,
    capability_error_to_workflow_error,
    run_workflow,
    workspace_error_to_workflow_error,
)
from luxar.application.state import WorkflowState
from luxar.domain.evidence import BuildDiagnostic, BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.project_analysis import ProjectAnalysis
from luxar.domain.repairs import FileReplacement, ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement
from luxar.ports.errors import CapabilityError, CapabilityErrorCategory
from luxar.ports.espidf_errors import EspIdfError, EspIdfErrorCategory
from luxar.ports.workspace_errors import (
    WorkspaceError,
    WorkspaceErrorCategory,
)


class RaisingRequirementParser:
    def __init__(self, error: CapabilityError) -> None:
        self.error = error

    def parse(self, task_text: str) -> FirmwareRequirement:
        raise self.error


class RaisingPlanner:
    def __init__(self, error: CapabilityError) -> None:
        self.error = error

    def create_plan(
        self,
        requirement: FirmwareRequirement,
        project_analysis: ProjectAnalysis | None = None,
    ) -> ExecutionPlan:
        del project_analysis
        raise self.error


class RaisingRepairPlanner:
    def __init__(self, error: CapabilityError) -> None:
        self.error = error

    def create_repair(
        self,
        requirement: FirmwareRequirement,
        plan: ExecutionPlan,
        evidence: BuildEvidence,
        files: list[ProjectFile],
        device_diagnosis=None,
    ) -> RepairPlan:
        raise self.error


class RaisingWorkspace:
    project_exists = True

    def __init__(self, error: WorkspaceError) -> None:
        self.error = error

    def read_project_files(
        self,
        project_path: Path,
    ) -> list[ProjectFile]:
        raise self.error

    def apply_repair(
        self,
        project_path: Path,
        repair: RepairPlan,
    ) -> list[str]:
        raise AssertionError("apply_repair must not run after read failure")


class RaisingEspIdf:
    def __init__(self, error: EspIdfError) -> None:
        self.error = error

    def build(self, project_path: Path) -> BuildEvidence:
        raise self.error


def make_requirement() -> FirmwareRequirement:
    return FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )


def make_plan() -> ExecutionPlan:
    return ExecutionPlan(
        steps=[
            PlanStep(
                kind="build_project",
                description="Build project",
            )
        ]
    )


def make_repair() -> RepairPlan:
    return RepairPlan(
        diagnosis="Fix source",
        replacements=[
            FileReplacement(
                path="main/main.c",
                content="fixed source",
            )
        ],
    )


def make_context(
    *,
    requirement_parser,
    planner,
    repair_planner,
    evidence_sequence: list[BuildEvidence],
    workspace=None,
) -> RuntimeContext:
    return RuntimeContext(
        requirement_parser=requirement_parser,
        planner=planner,
        repair_planner=repair_planner,
        espidf=FakeEspIdf(evidence_sequence),
        workspace=workspace
        or FakeWorkspace(
            [ProjectFile(path="main/main.c", content="broken source")]
        ),
        project_path=Path("workspace/blink"),
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        serial_port=None,
        checkpointer=InMemorySaver(),
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        monitor_timeout_seconds=10,
    )


@pytest.mark.parametrize(
    ("category", "expected_category"),
    [
        ("authentication", "authentication"),
        ("timeout", "timeout"),
        ("rate_limit", "rate_limit"),
        ("service", "service"),
        ("empty_response", "model_output"),
        ("invalid_json", "model_output"),
        ("invalid_schema", "model_output"),
    ],
)
def test_capability_error_mapping_uses_safe_application_text(
    category: CapabilityErrorCategory,
    expected_category: str,
) -> None:
    sensitive_marker = "SECRET_PROVIDER_BODY_123"
    capability_error = CapabilityError(
        category=category,
        message=sensitive_marker,
        retryable=True,
    )

    workflow_error = capability_error_to_workflow_error(
        capability_error,
        WorkflowState(task_text="build firmware"),
    )

    assert workflow_error.stage == "requirement_analysis"
    assert workflow_error.category == expected_category
    assert workflow_error.retryable is True
    assert workflow_error.message
    assert workflow_error.user_suggestion
    assert sensitive_marker not in workflow_error.message
    assert sensitive_marker not in workflow_error.user_suggestion


@pytest.mark.parametrize(
    ("state", "expected_stage"),
    [
        (WorkflowState(task_text="task"), "requirement_analysis"),
        (
            WorkflowState(
                task_text="task",
                requirement=make_requirement(),
            ),
            "project_analysis",
        ),
        (
            WorkflowState(
                task_text="task",
                requirement=make_requirement(),
                project_analysis=ProjectAnalysis(
                    project_exists=True,
                    has_source_code=True,
                    fingerprint="current",
                    summary="current project",
                ),
            ),
            "planning",
        ),
        (
            WorkflowState(
                task_text="task",
                requirement=make_requirement(),
                project_analysis=ProjectAnalysis(
                    project_exists=True,
                    has_source_code=True,
                    fingerprint="current",
                    summary="current project",
                ),
                plan=make_plan(),
            ),
            "repair",
        ),
    ],
)
def test_capability_error_mapping_derives_stage_from_latest_state(
    state: WorkflowState,
    expected_stage: str,
) -> None:
    workflow_error = capability_error_to_workflow_error(
        CapabilityError(
            category="service",
            message="hidden provider detail",
            retryable=True,
        ),
        state,
    )

    assert workflow_error.stage == expected_stage


def test_runner_returns_failed_state_for_requirement_error() -> None:
    context = make_context(
        requirement_parser=RaisingRequirementParser(
            CapabilityError(
                category="authentication",
                message="unsafe auth detail",
                retryable=False,
            )
        ),
        planner=FakePlanner(make_plan()),
        repair_planner=FakeRepairPlanner(make_repair()),
        evidence_sequence=[],
    )

    run_result = run_workflow(
        initial_state=WorkflowState(
            task_text="create ESP32 firmware",
            trace=[],
        ),
        context=context,
    )
    result = run_result.state

    assert result["task_text"] == "create ESP32 firmware"
    assert result["status"] == "failed"
    assert result["error"].stage == "requirement_analysis"
    assert result["error"].category == "authentication"
    assert result["trace"] == ["failed"]


def test_runner_preserves_requirement_when_planning_fails() -> None:
    requirement = make_requirement()
    context = make_context(
        requirement_parser=FakeRequirementParser(requirement),
        planner=RaisingPlanner(
            CapabilityError(
                category="invalid_schema",
                message="unsafe model payload",
                retryable=False,
            )
        ),
        repair_planner=FakeRepairPlanner(make_repair()),
        evidence_sequence=[],
    )

    run_result = run_workflow(
        initial_state=WorkflowState(task_text="blink GPIO 2", trace=[]),
        context=context,
    )
    result = run_result.state

    assert result["requirement"] is requirement
    assert result["status"] == "failed"
    assert result["error"].stage == "planning"
    assert result["error"].category == "model_output"
    assert result["trace"] == [
        "analyze_requirement",
        "analyze_project",
        "failed",
    ]


def test_runner_preserves_build_evidence_when_repair_fails() -> None:
    requirement = make_requirement()
    plan = make_plan()
    evidence = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=1,
        error_category="source",
        diagnostics=[
            BuildDiagnostic(
                file="main/main.c",
                line=42,
                column=5,
                severity="error",
                message="undeclared identifier",
            )
        ],
    )
    context = make_context(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        repair_planner=RaisingRepairPlanner(
            CapabilityError(
                category="service",
                message="unsafe service body",
                retryable=True,
            )
        ),
        evidence_sequence=[evidence],
    )

    run_result = run_workflow(
        initial_state=WorkflowState(
            task_text="repair ESP32 firmware",
            attempts=0,
            max_attempts=3,
            trace=[],
        ),
        context=context,
    )
    result = run_result.state

    assert result["requirement"] is requirement
    assert result["plan"] is plan
    assert result["build_evidence"] is evidence
    assert result["build_evidence"].diagnostics[0].line == 42
    assert result["attempts"] == 1
    assert result["status"] == "failed"
    assert result["error"].stage == "repair"
    assert result["error"].category == "service"
    assert result["trace"] == [
        "analyze_requirement",
        "analyze_project",
        "create_plan",
        "execute_next_step",
        "build_project",
        "failed",
    ]


def test_runner_keeps_successful_workflow_behavior() -> None:
    requirement = make_requirement()
    succeeded = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )
    context = make_context(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(make_plan()),
        repair_planner=FakeRepairPlanner(make_repair()),
        evidence_sequence=[succeeded],
    )

    run_result = run_workflow(
        initial_state=WorkflowState(
            task_text="build ESP32 firmware",
            attempts=0,
            max_attempts=3,
            trace=[],
        ),
        context=context,
    )
    result = run_result.state

    assert result["status"] == "completed"
    assert result["build_evidence"] is succeeded
    assert result["trace"] == [
        "analyze_requirement",
        "analyze_project",
        "create_plan",
        "execute_next_step",
        "build_project",
        "execute_next_step",
        "completed",
    ]


@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        ("invalid_project", False),
        ("unsafe_path", False),
        ("unsupported_file", False),
        ("file_too_large", False),
        ("context_too_large", False),
        ("invalid_encoding", False),
        ("io", True),
        ("rollback_failed", False),
    ],
)
def test_workspace_error_mapping_uses_safe_application_text(
    category: WorkspaceErrorCategory,
    retryable: bool,
) -> None:
    sensitive_marker = "SECRET_WORKSPACE_DETAIL_789"
    workspace_error = WorkspaceError(
        category=category,
        message=sensitive_marker,
        retryable=retryable,
    )

    workflow_error = workspace_error_to_workflow_error(
        workspace_error
    )

    assert workflow_error.stage == "repair"
    assert workflow_error.category == "workspace"
    assert workflow_error.retryable is retryable
    assert workflow_error.message
    assert workflow_error.user_suggestion
    assert sensitive_marker not in workflow_error.message
    assert sensitive_marker not in workflow_error.user_suggestion


def test_runner_preserves_latest_state_when_workspace_read_fails() -> None:
    requirement = make_requirement()
    plan = make_plan()
    evidence = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=1,
        error_category="source",
        diagnostics=[
            BuildDiagnostic(
                file="main/main.c",
                line=21,
                column=9,
                severity="error",
                message="undeclared identifier",
            )
        ],
    )
    context = make_context(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        repair_planner=FakeRepairPlanner(make_repair()),
        evidence_sequence=[evidence],
        workspace=RaisingWorkspace(
            WorkspaceError(
                category="unsafe_path",
                message="SECRET_ABSOLUTE_PATH",
                retryable=False,
            )
        ),
    )

    run_result = run_workflow(
        initial_state=WorkflowState(
            task_text="repair ESP32 firmware",
            attempts=0,
            max_attempts=3,
            trace=[],
        ),
        context=context,
    )
    result = run_result.state

    assert result["requirement"] is requirement
    assert "plan" not in result
    assert "build_evidence" not in result
    assert result["attempts"] == 0
    assert result["status"] == "failed"
    assert result["error"].stage == "project_analysis"
    assert result["error"].category == "workspace"
    assert "SECRET_ABSOLUTE_PATH" not in result["error"].message
    assert result["trace"] == [
        "analyze_requirement",
        "failed",
    ]


@pytest.mark.parametrize(
    ("category", "expected_category", "retryable"),
    [
        ("invalid_project", "environment", False),
        ("environment", "environment", False),
        ("dependency", "dependency", False),
        ("process", "environment", True),
    ],
)
def test_espidf_error_mapping_uses_safe_application_text(
    category: EspIdfErrorCategory,
    expected_category: str,
    retryable: bool,
) -> None:
    from luxar.application.runner import espidf_error_to_workflow_error

    error = EspIdfError(
        category=category,
        message="SECRET_ESPIDF_PATH",
        retryable=retryable,
    )
    workflow_error = espidf_error_to_workflow_error(
        error,
        {"pending_step_kind": "build_project"},
    )

    assert workflow_error.stage == "build"
    assert workflow_error.category == expected_category
    assert workflow_error.retryable is retryable
    assert "SECRET_ESPIDF_PATH" not in workflow_error.message
    assert "SECRET_ESPIDF_PATH" not in workflow_error.user_suggestion


def test_espidf_error_mapping_uses_project_creation_stage() -> None:
    from luxar.application.runner import espidf_error_to_workflow_error

    workflow_error = espidf_error_to_workflow_error(
        EspIdfError(
            category="invalid_project",
            message="SECRET_PATH",
            retryable=False,
        ),
        {"pending_step_kind": "create_project"},
    )

    assert workflow_error.stage == "project_creation"


def test_runner_preserves_latest_state_when_espidf_preflight_fails() -> None:
    requirement = make_requirement()
    plan = make_plan()
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(plan),
        repair_planner=FakeRepairPlanner(make_repair()),
        espidf=RaisingEspIdf(
            EspIdfError(
                category="dependency",
                message="SECRET_MANIFEST",
                retryable=False,
            )
        ),
        workspace=FakeWorkspace([]),
        project_path=Path("workspace/blink"),
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        serial_port=None,
        checkpointer=InMemorySaver(),
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        monitor_timeout_seconds=10,
    )

    run_result = run_workflow(
        initial_state=WorkflowState(
            task_text="build ESP32 firmware",
            attempts=0,
            max_attempts=3,
            trace=[],
        ),
        context=context,
    )
    result = run_result.state

    assert result["requirement"] is requirement
    assert result["plan"] is plan
    assert "build_evidence" not in result
    assert result["attempts"] == 0
    assert result["status"] == "failed"
    assert result["error"].stage == "build"
    assert result["error"].category == "dependency"
    assert result["trace"] == [
        "analyze_requirement",
        "analyze_project",
        "create_plan",
        "execute_next_step",
        "failed",
    ]


def test_runner_reports_safe_progress_for_complete_repair_loop() -> None:
    requirement = make_requirement()
    failed_evidence = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=1,
        error_category="source",
    )
    succeeded_evidence = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )
    events: list[WorkflowProgress] = []
    context = make_context(
        requirement_parser=FakeRequirementParser(requirement),
        planner=FakePlanner(make_plan()),
        repair_planner=FakeRepairPlanner(make_repair()),
        evidence_sequence=[failed_evidence, succeeded_evidence],
    )

    run_result = run_workflow(
        initial_state=WorkflowState(
            task_text="SECRET_TASK_TEXT",
            attempts=0,
            max_attempts=3,
            trace=[],
        ),
        context=context,
        progress_reporter=events.append,
    )
    result = run_result.state

    assert result["status"] == "completed"
    assert [(event.stage, event.message, event.attempts) for event in events] == [
        ("requirement", "需求分析完成", 0),
        ("analysis", "当前项目代码分析完成", 0),
        ("planning", "执行计划已生成", 0),
        ("build", "已完成第 1 次构建", 1),
        ("repair", "已应用受限制的源码修复", 1),
        ("build", "已完成第 2 次构建", 2),
        ("completed", "工作流执行成功", 2),
    ]
    assert all(
        event.narrative
        for event in events
        if event.stage not in {"completed", "failed"}
    )
    assert set(vars(events[0])) == {
        "stage",
        "message",
        "attempts",
        "narrative",
    }
    assert "SECRET_TASK_TEXT" not in repr(events)
    assert str(context.project_path) not in repr(events)


def test_runner_reports_one_failed_event_for_caught_espidf_error() -> None:
    events: list[WorkflowProgress] = []
    context = RuntimeContext(
        requirement_parser=FakeRequirementParser(make_requirement()),
        planner=FakePlanner(make_plan()),
        repair_planner=FakeRepairPlanner(make_repair()),
        espidf=RaisingEspIdf(
            EspIdfError(
                category="dependency",
                message="SECRET_MANIFEST",
                retryable=False,
            )
        ),
        workspace=FakeWorkspace([]),
        project_path=Path("SECRET_PROJECT_PATH"),
        project_creator=FakeProjectCreator([]),
        target_chip=None,
        flasher=FakeFlasher([]),
        serial_port=None,
        checkpointer=InMemorySaver(),
        monitor=FakeMonitor([]),
        log_analyst=FakeLogAnalyst([]),
        monitor_timeout_seconds=10,
    )

    run_result = run_workflow(
        initial_state=WorkflowState(
            task_text="build firmware",
            attempts=0,
            max_attempts=3,
            trace=[],
        ),
        context=context,
        progress_reporter=events.append,
    )
    result = run_result.state

    assert result["status"] == "failed"
    assert [(event.stage, event.message, event.attempts) for event in events] == [
        ("requirement", "需求分析完成", 0),
        ("analysis", "当前项目代码分析完成", 0),
        ("planning", "执行计划已生成", 0),
        ("failed", "工作流执行失败", 0),
    ]
    assert all(
        event.narrative
        for event in events
        if event.stage not in {"completed", "failed"}
    )
    assert "SECRET_MANIFEST" not in repr(events)
    assert "SECRET_PROJECT_PATH" not in repr(events)


def test_runner_does_not_normalize_reporter_exception() -> None:
    reporter_error = CapabilityError(
        category="service",
        message="REPORTER_PROGRAMMING_ERROR",
        retryable=False,
    )

    def raising_reporter(_: WorkflowProgress) -> None:
        raise reporter_error

    context = make_context(
        requirement_parser=FakeRequirementParser(make_requirement()),
        planner=FakePlanner(make_plan()),
        repair_planner=FakeRepairPlanner(make_repair()),
        evidence_sequence=[],
    )

    with pytest.raises(CapabilityError) as captured:
        run_workflow(
            initial_state=WorkflowState(
                task_text="build firmware",
                trace=[],
            ),
            context=context,
            progress_reporter=raising_reporter,
        )

    assert captured.value is reporter_error
