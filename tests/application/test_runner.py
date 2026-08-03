from pathlib import Path

import pytest

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_repair_planner import FakeRepairPlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.application.context import RuntimeContext
from luxar.application.runner import (
    capability_error_to_workflow_error,
    run_workflow,
)
from luxar.application.state import WorkflowState
from luxar.domain.evidence import BuildDiagnostic, BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.repairs import FileReplacement, ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement
from luxar.ports.errors import CapabilityError, CapabilityErrorCategory


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
    ) -> ExecutionPlan:
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
    ) -> RepairPlan:
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
) -> RuntimeContext:
    return RuntimeContext(
        requirement_parser=requirement_parser,
        planner=planner,
        repair_planner=repair_planner,
        espidf=FakeEspIdf(evidence_sequence),
        workspace=FakeWorkspace(
            [ProjectFile(path="main/main.c", content="broken source")]
        ),
        project_path=Path("workspace/blink"),
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
            "planning",
        ),
        (
            WorkflowState(
                task_text="task",
                requirement=make_requirement(),
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

    result = run_workflow(
        initial_state=WorkflowState(
            task_text="create ESP32 firmware",
            trace=[],
        ),
        context=context,
    )

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

    result = run_workflow(
        initial_state=WorkflowState(task_text="blink GPIO 2", trace=[]),
        context=context,
    )

    assert result["requirement"] is requirement
    assert result["status"] == "failed"
    assert result["error"].stage == "planning"
    assert result["error"].category == "model_output"
    assert result["trace"] == ["analyze_requirement", "failed"]


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

    result = run_workflow(
        initial_state=WorkflowState(
            task_text="repair ESP32 firmware",
            attempts=0,
            max_attempts=3,
            trace=[],
        ),
        context=context,
    )

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
        "create_plan",
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

    result = run_workflow(
        initial_state=WorkflowState(
            task_text="build ESP32 firmware",
            attempts=0,
            max_attempts=3,
            trace=[],
        ),
        context=context,
    )

    assert result["status"] == "completed"
    assert result["build_evidence"] is succeeded
    assert result["trace"] == [
        "analyze_requirement",
        "create_plan",
        "build_project",
        "completed",
    ]
