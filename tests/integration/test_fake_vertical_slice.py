from pathlib import Path

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_repair_planner import FakeRepairPlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.application.context import RuntimeContext
from luxar.application.graph import build_graph
from luxar.domain.evidence import BuildDiagnostic, BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.repairs import FileReplacement, ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


def make_fixture(
    *,
    requirement: FirmwareRequirement,
    evidence_sequence: list[BuildEvidence],
) -> tuple[
    RuntimeContext,
    FakeRequirementParser,
    FakePlanner,
    FakeRepairPlanner,
    FakeEspIdf,
    FakeWorkspace,
    ExecutionPlan,
    RepairPlan,
]:
    plan = ExecutionPlan(
        steps=[PlanStep(kind="build_project", description="Build project")]
    )
    repair = RepairPlan(
        diagnosis="declare and configure the GPIO correctly",
        replacements=[
            FileReplacement(path="main/main.c", content="fixed source")
        ],
    )
    parser = FakeRequirementParser(requirement)
    planner = FakePlanner(plan)
    repair_planner = FakeRepairPlanner(repair)
    espidf = FakeEspIdf(evidence_sequence)
    workspace = FakeWorkspace(
        [ProjectFile(path="main/main.c", content="broken source")]
    )
    context = RuntimeContext(
        requirement_parser=parser,
        planner=planner,
        espidf=espidf,
        project_path=Path("workspace/blink"),
        repair_planner=repair_planner,
        workspace=workspace,
    )
    return (
        context,
        parser,
        planner,
        repair_planner,
        espidf,
        workspace,
        plan,
        repair,
    )


def test_source_failure_is_repaired_rebuilt_and_completed() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )
    failed = BuildEvidence(
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
                message="'gpio_num' undeclared",
            )
        ],
    )
    succeeded = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )
    (
        context,
        parser,
        planner,
        repair_planner,
        espidf,
        workspace,
        plan,
        repair,
    ) = make_fixture(
        requirement=requirement,
        evidence_sequence=[failed, succeeded],
    )

    result = build_graph().invoke(
        {
            "task_text": "create an ESP32 GPIO blink project",
            "attempts": 0,
            "max_attempts": 3,
            "trace": [],
        },
        context=context,
    )

    assert result["status"] == "completed"
    assert result["attempts"] == 2
    assert result["build_evidence"] is succeeded
    assert result["repair_plan"] is repair
    assert result["changed_files"] == ["main/main.c"]
    assert result["trace"] == [
        "analyze_requirement",
        "create_plan",
        "execute_next_step",
        "build_project",
        "repair_project",
        "build_project",
        "execute_next_step",
        "completed",
    ]
    assert parser.calls == ["create an ESP32 GPIO blink project"]
    assert planner.calls == [requirement]
    assert espidf.calls == [context.project_path, context.project_path]
    assert workspace.read_calls == [context.project_path]
    assert workspace.apply_calls == [(context.project_path, repair)]
    assert repair_planner.calls == [
        (
            requirement,
            plan,
            failed,
            [ProjectFile(path="main/main.c", content="broken source")],
        )
    ]


def test_incomplete_requirement_stops_before_planning_or_building() -> None:
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        missing_fields=["gpio"],
    )
    context, _, planner, repair_planner, espidf, workspace, _, _ = make_fixture(
        requirement=requirement,
        evidence_sequence=[],
    )

    result = build_graph().invoke(
        {"task_text": "make GPIO blink", "trace": []},
        context=context,
    )

    assert result["status"] == "needs_clarification"
    assert result["trace"] == [
        "analyze_requirement",
        "request_clarification",
    ]
    assert planner.calls == []
    assert repair_planner.calls == []
    assert espidf.calls == []
    assert workspace.read_calls == []


def test_timeout_retries_without_repair_then_completes() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    timeout = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=124,
        error_category="timeout",
    )
    succeeded = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )
    context, _, _, repair_planner, espidf, workspace, _, _ = make_fixture(
        requirement=requirement,
        evidence_sequence=[timeout, succeeded],
    )

    result = build_graph().invoke(
        {
            "task_text": "build firmware",
            "attempts": 0,
            "max_attempts": 2,
            "trace": [],
        },
        context=context,
    )

    assert result["status"] == "completed"
    assert result["attempts"] == 2
    assert result["trace"] == [
        "analyze_requirement",
        "create_plan",
        "execute_next_step",
        "build_project",
        "build_project",
        "execute_next_step",
        "completed",
    ]
    assert len(espidf.calls) == 2
    assert repair_planner.calls == []
    assert workspace.read_calls == []


def test_environment_failure_terminates_without_repair() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    failed = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=127,
        error_category="environment",
    )
    context, _, _, repair_planner, espidf, workspace, _, _ = make_fixture(
        requirement=requirement,
        evidence_sequence=[failed],
    )

    result = build_graph().invoke(
        {
            "task_text": "build firmware",
            "attempts": 0,
            "max_attempts": 3,
            "trace": [],
        },
        context=context,
    )

    assert result["status"] == "failed"
    assert result["attempts"] == 1
    assert result["build_evidence"] is failed
    assert len(espidf.calls) == 1
    assert repair_planner.calls == []
    assert workspace.read_calls == []


def test_stream_reports_repair_loop_node_order() -> None:
    requirement = FirmwareRequirement(target="esp32", feature="gpio_blink")
    failed = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=1,
        error_category="source",
    )
    succeeded = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )
    context, *_ = make_fixture(
        requirement=requirement,
        evidence_sequence=[failed, succeeded],
    )

    updates = list(
        build_graph().stream(
            {
                "task_text": "build firmware",
                "attempts": 0,
                "max_attempts": 3,
                "trace": [],
            },
            context=context,
            stream_mode="updates",
        )
    )

    assert [next(iter(update)) for update in updates] == [
        "analyze_requirement",
        "create_plan",
        "execute_next_step",
        "build_project",
        "repair_project",
        "build_project",
        "execute_next_step",
        "completed",
    ]
