from __future__ import annotations

from luxar.application.runtime_comparison import (
    RuntimeComparisonScenario,
    RuntimeExecutionSnapshot,
    normalize_runtime_outcome,
    qualification_observation_from_comparison,
    run_runtime_comparison,
)
from luxar.domain.agent.tasks import AgentTask, AgentTaskGraph
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep


def _successful_build() -> BuildEvidence:
    return BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )


def _scenario() -> RuntimeComparisonScenario:
    return RuntimeComparisonScenario(
        scenario_id="mqtt-topic",
        objective="修改 MQTT 主题并保留传感器能力",
        allowed_changed_paths=["components/network/mqtt.c"],
        preserved_capability_ids=["sensor.sht30"],
    )


def test_comparison_runs_both_runtimes_and_reports_auditable_pass() -> None:
    calls: list[str] = []
    build = _successful_build()

    def legacy_runner(
        scenario: RuntimeComparisonScenario,
    ) -> RuntimeExecutionSnapshot:
        calls.append(f"legacy:{scenario.scenario_id}")
        return RuntimeExecutionSnapshot(
            state={
                "status": "completed",
                "plan": ExecutionPlan(
                    steps=[PlanStep(kind="build_project", description="build")]
                ),
                "build_evidence": build,
                "changed_files": ["components/network/mqtt.c"],
            },
            capability_ids=["sensor.sht30", "network.mqtt_client"],
        )

    def supervisor_runner(
        scenario: RuntimeComparisonScenario,
    ) -> RuntimeExecutionSnapshot:
        calls.append(f"supervisor:{scenario.scenario_id}")
        return RuntimeExecutionSnapshot(
            state={
                "status": "completed",
                "acceptance_passed": True,
                "build_verified": True,
                "build_evidence": build,
                "evidence_ids": ["bundle:mqtt", "build:mqtt:verify"],
                "task_graph": AgentTaskGraph(
                    objective_id="mqtt-topic",
                    tasks=[
                        AgentTask(
                            task_id="inspect",
                            kind="inspect_project",
                            title="inspect",
                            description="inspect",
                        ),
                        AgentTask(
                            task_id="architecture",
                            parent_id="inspect",
                            kind="architecture_plan",
                            title="architecture",
                            description="architecture",
                            depends_on=["inspect"],
                        ),
                        AgentTask(
                            task_id="code",
                            parent_id="architecture",
                            kind="code_change",
                            title="code",
                            description="code",
                            depends_on=["architecture"],
                        ),
                    ],
                ),
            },
            capability_ids=["sensor.sht30", "network.mqtt_client"],
            changed_files=["components/network/mqtt.c"],
        )

    report = run_runtime_comparison(
        _scenario(),
        legacy_runner=legacy_runner,
        supervisor_runner=supervisor_runner,
    )

    assert calls == ["legacy:mqtt-topic", "supervisor:mqtt-topic"]
    assert report.supervisor_not_worse is True
    assert report.failed_check_ids == []
    assert report.evidence_id.startswith("runtime-comparison:")
    observation = qualification_observation_from_comparison(report)
    assert observation.gate_id == "runtime_comparison"
    assert observation.passed is True
    assert observation.evidence_ids == [report.evidence_id]


def test_missing_preserved_capability_blocks_comparison_qualification() -> None:
    scenario = _scenario()
    build = _successful_build()

    report = run_runtime_comparison(
        scenario,
        legacy_runner=lambda _: RuntimeExecutionSnapshot(
            state={
                "status": "completed",
                "plan": ExecutionPlan(
                    steps=[PlanStep(kind="build_project", description="build")]
                ),
                "build_evidence": build,
            },
            capability_ids=["sensor.sht30"],
        ),
        supervisor_runner=lambda _: RuntimeExecutionSnapshot(
            state={
                "status": "completed",
                "acceptance_passed": True,
                "build_verified": True,
                "build_evidence": build,
                "evidence_ids": ["build:verify"],
                "task_graph": AgentTaskGraph(
                    objective_id="mqtt-topic",
                    tasks=[
                        AgentTask(
                            task_id="inspect",
                            kind="inspect_project",
                            title="inspect",
                            description="inspect",
                        ),
                        AgentTask(
                            task_id="verify",
                            parent_id="inspect",
                            kind="verify_acceptance",
                            title="verify",
                            description="verify",
                            depends_on=["inspect"],
                        ),
                    ],
                ),
            },
            capability_ids=[],
        ),
    )

    assert report.supervisor_not_worse is False
    assert "capability_preservation" in report.failed_check_ids


def test_build_success_cannot_be_normalized_as_hardware_verification() -> None:
    scenario = RuntimeComparisonScenario(
        scenario_id="hardware-smoke",
        objective="验证真实设备功能",
        require_build=True,
        require_hardware=True,
    )

    outcome = normalize_runtime_outcome(
        "supervisor",
        scenario,
        RuntimeExecutionSnapshot(
            state={
                "status": "completed",
                "acceptance_passed": True,
                "build_verified": True,
                "build_evidence": _successful_build(),
                "hardware_function_verified": True,
                "evidence_ids": ["build:verify"],
            }
        ),
    )

    assert outcome.build_verified is True
    assert outcome.hardware_verified is False
