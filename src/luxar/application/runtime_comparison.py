"""Evidence-backed comparison between the legacy and Supervisor runtimes.

The comparison deliberately consumes the outputs of real runtime executions
instead of assigning scores to architecture features.  Callers run the same
scenario against isolated project copies and return an execution snapshot for
each runtime.  The report then checks completion, acceptance, file scope,
capability preservation, task structure, build/device proof and approvals.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.agent.code_changes import ChangeBundleValidation
from luxar.domain.agent.tasks import AgentTaskGraph
from luxar.domain.devices import DeviceDiagnosis, MonitorEvidence
from luxar.domain.evidence import BuildEvidence
from luxar.domain.plans import ExecutionPlan

if TYPE_CHECKING:
    from luxar.application.runtime_qualification import QualificationObservation


RuntimeName = Literal["legacy", "supervisor"]
ModelT = TypeVar("ModelT", bound=BaseModel)


class RuntimeComparisonScenario(BaseModel):
    """One objective executed independently by both runtimes."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scenario_id: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=4000)
    allowed_changed_paths: list[str] = Field(default_factory=list, max_length=200)
    preserved_capability_ids: list[str] = Field(default_factory=list, max_length=200)
    require_build: bool = True
    require_hardware: bool = False
    require_approval: bool = False


@dataclass(frozen=True)
class RuntimeExecutionSnapshot:
    """Raw runtime state plus facts measured from its resulting workspace."""

    state: Mapping[str, object]
    capability_ids: Sequence[str] = field(default_factory=tuple)
    changed_files: Sequence[str] = field(default_factory=tuple)


RuntimeScenarioRunner = Callable[
    [RuntimeComparisonScenario], RuntimeExecutionSnapshot
]


class RuntimeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    runtime: RuntimeName
    status: str
    completed: bool
    acceptance_passed: bool
    changed_files: list[str] = Field(default_factory=list)
    missing_preserved_capability_ids: list[str] = Field(default_factory=list)
    task_count: int = Field(ge=0)
    task_depth: int = Field(ge=0)
    build_verified: bool
    hardware_verified: bool
    approval_enforced: bool
    evidence_ids: list[str] = Field(default_factory=list)


class RuntimeComparisonCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    check_id: str
    passed: bool
    applicable: bool = True
    note: str = ""


class RuntimeComparisonReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scenario_id: str
    legacy: RuntimeOutcome
    supervisor: RuntimeOutcome
    checks: list[RuntimeComparisonCheck]
    supervisor_not_worse: bool
    evidence_id: str

    @property
    def failed_check_ids(self) -> list[str]:
        return [
            check.check_id
            for check in self.checks
            if check.applicable and not check.passed
        ]


def _model(value: object, model_type: type[ModelT]) -> ModelT | None:
    if isinstance(value, model_type):
        return value
    if isinstance(value, Mapping):
        return model_type.model_validate(value)
    return None


def _task_metrics(runtime: RuntimeName, state: Mapping[str, object]) -> tuple[int, int]:
    if runtime == "legacy":
        plan = _model(state.get("plan"), ExecutionPlan)
        if not isinstance(plan, ExecutionPlan):
            return 0, 0
        return len(plan.steps), 1 if plan.steps else 0

    graph = _model(state.get("task_graph"), AgentTaskGraph)
    if not isinstance(graph, AgentTaskGraph):
        return 0, 0
    parents = {task.task_id: task.parent_id for task in graph.tasks}

    def depth(task_id: str) -> int:
        value = 1
        parent_id = parents[task_id]
        while parent_id is not None:
            value += 1
            parent_id = parents[parent_id]
        return value

    return len(graph.tasks), max((depth(task_id) for task_id in parents), default=0)


def _changed_files(
    runtime: RuntimeName,
    state: Mapping[str, object],
    measured: Sequence[str],
) -> list[str]:
    paths = [str(path) for path in measured]
    raw = state.get("changed_files", [])
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        paths.extend(str(path) for path in raw)
    if runtime == "supervisor":
        validations = state.get("change_validations", {})
        if isinstance(validations, Mapping):
            for value in validations.values():
                validation = _model(value, ChangeBundleValidation)
                if isinstance(validation, ChangeBundleValidation):
                    paths.extend(validation.changed_files)
    return sorted(set(paths))


def _evidence_ids(runtime: RuntimeName, state: Mapping[str, object]) -> list[str]:
    evidence: list[str] = []
    raw = state.get("evidence_ids", [])
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        evidence.extend(str(item) for item in raw if str(item).strip())

    build = _model(state.get("build_evidence"), BuildEvidence)
    if isinstance(build, BuildEvidence) and build.success:
        evidence.append(f"{runtime}:build")
    if runtime == "legacy":
        changed_files = state.get("changed_files", [])
        if isinstance(changed_files, Sequence) and not isinstance(
            changed_files,
            (str, bytes),
        ):
            for path in changed_files:
                evidence.append(f"legacy:change:{path}")
        diagnosis = _model(state.get("device_diagnosis"), DeviceDiagnosis)
        monitor = _model(state.get("monitor_evidence"), MonitorEvidence)
        if (
            isinstance(diagnosis, DeviceDiagnosis)
            and diagnosis.healthy
            and isinstance(monitor, MonitorEvidence)
        ):
            evidence.append("legacy:device")
    return list(dict.fromkeys(evidence))


def normalize_runtime_outcome(
    runtime: RuntimeName,
    scenario: RuntimeComparisonScenario,
    execution: RuntimeExecutionSnapshot,
) -> RuntimeOutcome:
    """Convert either runtime's state into the shared comparison contract."""

    state = execution.state
    status = str(state.get("status", "unknown"))
    completed = status == "completed"
    build = _model(state.get("build_evidence"), BuildEvidence)
    build_has_tool_evidence = isinstance(build, BuildEvidence) and build.success
    build_verified = (
        bool(state.get("build_verified", False)) and build_has_tool_evidence
        if runtime == "supervisor"
        else build_has_tool_evidence
    )
    if runtime == "supervisor":
        monitor = _model(state.get("monitor_evidence"), MonitorEvidence)
        hardware_verified = bool(
            state.get("hardware_function_verified", False)
            and isinstance(monitor, MonitorEvidence)
        )
        acceptance_passed = bool(state.get("acceptance_passed", False))
    else:
        diagnosis = _model(state.get("device_diagnosis"), DeviceDiagnosis)
        monitor = _model(state.get("monitor_evidence"), MonitorEvidence)
        hardware_verified = bool(
            isinstance(diagnosis, DeviceDiagnosis)
            and diagnosis.healthy
            and isinstance(monitor, MonitorEvidence)
        )
        acceptance_passed = completed

    if scenario.require_approval:
        approval_enforced = state.get("approval_status") == "approved"
    else:
        approval_enforced = True

    task_count, task_depth = _task_metrics(runtime, state)
    capabilities = {str(item) for item in execution.capability_ids}
    missing = sorted(set(scenario.preserved_capability_ids) - capabilities)
    return RuntimeOutcome(
        runtime=runtime,
        status=status,
        completed=completed,
        acceptance_passed=acceptance_passed,
        changed_files=_changed_files(
            runtime,
            state,
            execution.changed_files,
        ),
        missing_preserved_capability_ids=missing,
        task_count=task_count,
        task_depth=task_depth,
        build_verified=build_verified,
        hardware_verified=hardware_verified,
        approval_enforced=approval_enforced,
        evidence_ids=_evidence_ids(runtime, state),
    )


def compare_runtime_outcomes(
    scenario: RuntimeComparisonScenario,
    legacy: RuntimeOutcome,
    supervisor: RuntimeOutcome,
) -> RuntimeComparisonReport:
    """Apply deterministic non-regression checks to two completed runs."""

    allowed = set(scenario.allowed_changed_paths)
    checks = [
        RuntimeComparisonCheck(
            check_id="terminal_completion",
            passed=legacy.completed and supervisor.completed,
            note=f"legacy={legacy.status}; supervisor={supervisor.status}",
        ),
        RuntimeComparisonCheck(
            check_id="acceptance",
            passed=legacy.acceptance_passed and supervisor.acceptance_passed,
        ),
        RuntimeComparisonCheck(
            check_id="changed_path_scope",
            passed=(
                set(legacy.changed_files) <= allowed
                and set(supervisor.changed_files) <= allowed
            ),
            applicable=bool(allowed),
            note=(
                f"legacy={legacy.changed_files}; supervisor={supervisor.changed_files}"
            ),
        ),
        RuntimeComparisonCheck(
            check_id="capability_preservation",
            passed=(
                not legacy.missing_preserved_capability_ids
                and not supervisor.missing_preserved_capability_ids
            ),
            applicable=bool(scenario.preserved_capability_ids),
            note=(
                "legacy_missing="
                f"{legacy.missing_preserved_capability_ids}; supervisor_missing="
                f"{supervisor.missing_preserved_capability_ids}"
            ),
        ),
        RuntimeComparisonCheck(
            check_id="task_structure",
            passed=(
                supervisor.task_count >= legacy.task_count
                and supervisor.task_depth > legacy.task_depth
            ),
            note=(
                f"legacy={legacy.task_count}/{legacy.task_depth}; "
                f"supervisor={supervisor.task_count}/{supervisor.task_depth}"
            ),
        ),
        RuntimeComparisonCheck(
            check_id="build_evidence",
            passed=legacy.build_verified and supervisor.build_verified,
            applicable=scenario.require_build,
        ),
        RuntimeComparisonCheck(
            check_id="hardware_evidence",
            passed=legacy.hardware_verified and supervisor.hardware_verified,
            applicable=scenario.require_hardware,
        ),
        RuntimeComparisonCheck(
            check_id="approval_policy",
            passed=legacy.approval_enforced and supervisor.approval_enforced,
            applicable=scenario.require_approval,
        ),
        RuntimeComparisonCheck(
            check_id="supervisor_completion_evidence",
            passed=bool(supervisor.evidence_ids),
        ),
    ]
    passed = all(check.passed for check in checks if check.applicable)
    digest_payload = "\n".join(
        (
            scenario.model_dump_json(),
            legacy.model_dump_json(),
            supervisor.model_dump_json(),
            *(check.model_dump_json() for check in checks),
        )
    )
    evidence_id = "runtime-comparison:" + hashlib.sha256(
        digest_payload.encode("utf-8")
    ).hexdigest()
    return RuntimeComparisonReport(
        scenario_id=scenario.scenario_id,
        legacy=legacy,
        supervisor=supervisor,
        checks=checks,
        supervisor_not_worse=passed,
        evidence_id=evidence_id,
    )


def run_runtime_comparison(
    scenario: RuntimeComparisonScenario,
    *,
    legacy_runner: RuntimeScenarioRunner,
    supervisor_runner: RuntimeScenarioRunner,
) -> RuntimeComparisonReport:
    """Execute the same scenario twice and return one auditable report."""

    legacy = normalize_runtime_outcome("legacy", scenario, legacy_runner(scenario))
    supervisor = normalize_runtime_outcome(
        "supervisor",
        scenario,
        supervisor_runner(scenario),
    )
    return compare_runtime_outcomes(scenario, legacy, supervisor)


def qualification_observation_from_comparison(
    report: RuntimeComparisonReport,
) -> "QualificationObservation":
    """Translate a comparison report into the Stage 10 release-gate input."""

    # Local import keeps the comparison contract usable without making the
    # qualification module depend on the runtime adapters.
    from luxar.application.runtime_qualification import QualificationObservation

    return QualificationObservation(
        gate_id="runtime_comparison",
        passed=report.supervisor_not_worse,
        evidence_ids=[report.evidence_id] if report.supervisor_not_worse else [],
        note=(
            "legacy/supervisor comparison passed"
            if report.supervisor_not_worse
            else "failed checks: " + ", ".join(report.failed_check_ids)
        ),
    )


__all__ = [
    "RuntimeComparisonCheck",
    "RuntimeComparisonReport",
    "RuntimeComparisonScenario",
    "RuntimeExecutionSnapshot",
    "RuntimeOutcome",
    "compare_runtime_outcomes",
    "normalize_runtime_outcome",
    "qualification_observation_from_comparison",
    "run_runtime_comparison",
]
