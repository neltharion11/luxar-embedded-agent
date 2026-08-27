"""可持久化、带依赖的项目级任务图。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from luxar.domain.agent.acceptance import AcceptanceCriterion
from luxar.domain.agent.changes import ChangeSet
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.verification import VerificationPlan


TaskStatus = Literal[
    "pending",
    "ready",
    "running",
    "blocked",
    "passed",
    "failed",
    "cancelled",
]


class AgentTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: str = Field(min_length=1, max_length=240)
    parent_id: str | None = None
    kind: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=4000)
    depends_on: list[str] = Field(default_factory=list, max_length=40)
    status: TaskStatus = "pending"
    input_refs: list[str] = Field(default_factory=list, max_length=80)
    expected_outputs: list[str] = Field(default_factory=list, max_length=80)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=40)
    allowed_tools: list[str] = Field(default_factory=list, max_length=40)
    allowed_paths: list[str] = Field(default_factory=list, max_length=80)
    preserves: list[str] = Field(default_factory=list, max_length=80)
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=2, ge=1, le=20)
    requires_approval: bool = False


class AgentTaskGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    objective_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(default=1, ge=1)
    tasks: list[AgentTask] = Field(min_length=1, max_length=200)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_graph(self) -> "AgentTaskGraph":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task graph task IDs must be unique")
        known = set(task_ids)
        for task in self.tasks:
            if task.parent_id is not None and task.parent_id not in known:
                raise ValueError(f"unknown parent task: {task.parent_id}")
            unknown_dependencies = set(task.depends_on) - known
            if unknown_dependencies:
                raise ValueError(
                    f"unknown task dependencies: {sorted(unknown_dependencies)}"
                )
            if task.task_id in task.depends_on:
                raise ValueError("task cannot depend on itself")
        self._assert_acyclic()
        criterion_ids = [criterion.criterion_id for criterion in self.acceptance_criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("acceptance criterion IDs must be unique")
        return self

    def _assert_acyclic(self) -> None:
        dependencies = {task.task_id: set(task.depends_on) for task in self.tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("task graph dependencies must be acyclic")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in dependencies:
            visit(task_id)

    def ready_tasks(self) -> list[AgentTask]:
        by_id = {task.task_id: task for task in self.tasks}
        ready: list[AgentTask] = []
        for task in self.tasks:
            if task.status not in {"pending", "ready"}:
                continue
            dependencies = [by_id[task_id] for task_id in task.depends_on]
            if all(dependency.status == "passed" for dependency in dependencies):
                ready.append(task.model_copy(update={"status": "ready"}))
        return ready

    def update_task(self, task_id: str, **updates: object) -> "AgentTaskGraph":
        if task_id not in {task.task_id for task in self.tasks}:
            raise KeyError(task_id)
        tasks = [
            task.model_copy(update=updates) if task.task_id == task_id else task
            for task in self.tasks
        ]
        return self.model_copy(update={"tasks": tasks})

    @property
    def all_tasks_passed(self) -> bool:
        return all(task.status == "passed" for task in self.tasks)

    @property
    def has_blocking_task(self) -> bool:
        return any(task.status in {"blocked", "failed"} for task in self.tasks)


class TaskGraphDiff(BaseModel):
    """两个计划版本之间的可展示差异。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    from_revision: int = Field(ge=1)
    to_revision: int = Field(ge=1)
    added_task_ids: list[str] = Field(default_factory=list, max_length=200)
    removed_task_ids: list[str] = Field(default_factory=list, max_length=200)
    changed_task_ids: list[str] = Field(default_factory=list, max_length=200)
    preserved_task_ids: list[str] = Field(default_factory=list, max_length=200)


class TaskGraphRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    graph: AgentTaskGraph
    diff: TaskGraphDiff


def _task_plan_signature(task: AgentTask) -> tuple[object, ...]:
    """忽略运行时 status/attempts，只比较计划语义。"""

    return (
        task.parent_id,
        task.kind,
        task.title,
        task.description,
        tuple(task.depends_on),
        tuple(task.input_refs),
        tuple(task.expected_outputs),
        tuple(task.acceptance_criteria),
        tuple(task.allowed_tools),
        tuple(task.allowed_paths),
        tuple(task.preserves),
        task.max_attempts,
        task.requires_approval,
    )


def diff_task_graph(previous: AgentTaskGraph, current: AgentTaskGraph) -> TaskGraphDiff:
    previous_tasks = {task.task_id: task for task in previous.tasks}
    current_tasks = {task.task_id: task for task in current.tasks}
    added = sorted(set(current_tasks) - set(previous_tasks))
    removed = sorted(set(previous_tasks) - set(current_tasks))
    changed = sorted(
        task_id
        for task_id in set(previous_tasks) & set(current_tasks)
        if _task_plan_signature(previous_tasks[task_id])
        != _task_plan_signature(current_tasks[task_id])
    )
    preserved = sorted(
        task_id
        for task_id in set(previous_tasks) & set(current_tasks)
        if task_id not in changed
    )
    return TaskGraphDiff(
        from_revision=previous.revision,
        to_revision=current.revision,
        added_task_ids=added,
        removed_task_ids=removed,
        changed_task_ids=changed,
        preserved_task_ids=preserved,
    )


def revise_task_graph(
    previous: AgentTaskGraph,
    objective: ProjectObjective,
    change_set: ChangeSet,
) -> TaskGraphRevision:
    """基于新目标生成计划，并保留未受影响任务的执行状态。"""

    previous_tasks = {task.task_id: task for task in previous.tasks}
    previous_path_scopes = {
        task.input_refs[0]: task.allowed_paths
        for task in previous.tasks
        if task.kind == "code_change"
        and task.input_refs
        and task.allowed_paths
    }
    proposed = build_task_graph(
        objective,
        change_set,
        allowed_paths_by_capability=previous_path_scopes,
    )
    tasks: list[AgentTask] = []
    for task in proposed.tasks:
        old = previous_tasks.get(task.task_id)
        if old is not None and _task_plan_signature(old) == _task_plan_signature(task):
            task = task.model_copy(
                update={"status": old.status, "attempts": old.attempts}
            )
        tasks.append(task)
    previous_criteria = {
        criterion.criterion_id: criterion for criterion in previous.acceptance_criteria
    }
    criteria = [
        criterion.model_copy(
            update={
                "status": previous_criteria[criterion.criterion_id].status,
                "evidence_ids": previous_criteria[criterion.criterion_id].evidence_ids,
            }
        )
        if criterion.criterion_id in previous_criteria
        else criterion
        for criterion in proposed.acceptance_criteria
    ]
    current = proposed.model_copy(update={"tasks": tasks, "acceptance_criteria": criteria})
    return TaskGraphRevision(graph=current, diff=diff_task_graph(previous, current))


def build_task_graph(
    objective: ProjectObjective,
    change_set: ChangeSet,
    *,
    current_capability_ids: Iterable[str] = (),
    allowed_paths_by_capability: Mapping[str, Sequence[str]] | None = None,
    verification_plan: VerificationPlan | dict[str, object] | None = None,
) -> AgentTaskGraph:
    """为第一批闭环生成 inspect → architecture → code → verify 分层任务。"""

    prefix = objective.objective_id
    inspect_id = f"{prefix}:inspect"
    architecture_id = f"{prefix}:architecture"
    tasks = [
        AgentTask(
            task_id=inspect_id,
            kind="inspect_project",
            title="检查现有工程",
            description="读取源码快照并提取已有能力，作为非回归基线。",
            expected_outputs=["project_capabilities"],
            allowed_tools=["workspace.read", "project.inspect"],
        ),
        AgentTask(
            task_id=architecture_id,
            parent_id=inspect_id,
            kind="architecture_plan",
            title="建立变更边界",
            description="根据目标、变更集和已有能力确定组件级实现边界。",
            depends_on=[inspect_id],
            input_refs=["project_capabilities", "change_set"],
            expected_outputs=["task_graph"],
            allowed_tools=["project.plan"],
        ),
    ]
    code_task_ids: list[str] = []
    path_scopes = allowed_paths_by_capability or {}
    if isinstance(verification_plan, dict):
        verification_plan = VerificationPlan.model_validate(verification_plan)
    for index, change in enumerate(change_set.changes):
        if change.operation not in {"add", "modify", "remove", "replace"}:
            continue
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", change.capability_id)
        change_key = f"{change.operation}:{slug}"
        task_id = f"{prefix}:code:{change_key}"
        code_task_ids.append(task_id)
        tasks.append(
            AgentTask(
                task_id=task_id,
                parent_id=architecture_id,
                kind="code_change",
                title=f"{change.operation} {change.capability_id}",
                description=change.rationale or f"实施 {change.capability_id} 的局部变更。",
                depends_on=[architecture_id],
                input_refs=[change.capability_id],
                expected_outputs=[f"source_change:{change.capability_id}"],
                acceptance_criteria=[f"{prefix}:criterion:{change_key}"],
                allowed_tools=["workspace.patch", "source.validate"],
                allowed_paths=list(path_scopes.get(change.capability_id, ())),
                preserves=[
                    item.capability_id
                    for item in change_set.changes
                    if item.operation == "preserve"
                ],
            )
        )

    verify_id = f"{prefix}:verify"
    verify_dependencies = code_task_ids or [architecture_id]
    tasks.append(
        AgentTask(
            task_id=verify_id,
            parent_id=architecture_id,
            kind="verify_acceptance",
            title="验证目标和非回归条件",
            description="根据工具证据检查变更目标、验收条件和 preserve 不变量。",
            depends_on=verify_dependencies,
            input_refs=["acceptance_criteria", "evidence"],
            expected_outputs=[
                "acceptance_result",
                *(
                    ["component_test_evidence"]
                    if verification_plan and verification_plan.component_tests
                    else []
                ),
                *(["build_evidence"] if verification_plan and verification_plan.require_build else []),
                *(["flash_evidence"] if verification_plan and verification_plan.require_flash else []),
                *(
                    ["firmware_resource_evidence"]
                    if verification_plan and verification_plan.firmware_assertions
                    else []
                ),
                *(["device_evidence"] if verification_plan and verification_plan.require_device else []),
                *(
                    ["protocol_probe_evidence"]
                    if verification_plan and verification_plan.protocol_probes
                    else []
                ),
                *(
                    ["runtime_scenario_evidence"]
                    if verification_plan and verification_plan.runtime_scenarios
                    else []
                ),
            ],
            allowed_tools=[
                "source.assert",
                "acceptance.verify",
                *(
                    ["component.test"]
                    if verification_plan and verification_plan.component_tests
                    else []
                ),
                *(["espidf.build"] if verification_plan and verification_plan.require_build else []),
                *(["device.flash"] if verification_plan and verification_plan.require_flash else []),
                *(
                    ["firmware.inspect"]
                    if verification_plan and verification_plan.firmware_assertions
                    else []
                ),
                *(["device.monitor"] if verification_plan and verification_plan.require_device else []),
                *(
                    ["protocol.probe"]
                    if verification_plan and verification_plan.protocol_probes
                    else []
                ),
                *(
                    ["runtime.scenario"]
                    if verification_plan and verification_plan.runtime_scenarios
                    else []
                ),
            ],
            requires_approval=bool(
                verification_plan and verification_plan.require_flash
            ),
        )
    )

    criteria: list[AcceptanceCriterion] = []
    for index, change in enumerate(change_set.changes):
        if change.operation not in {"add", "modify", "remove", "replace"}:
            continue
        criteria.append(
            AcceptanceCriterion(
                criterion_id=(
                    f"{prefix}:criterion:{change.operation}:"
                    f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', change.capability_id)}"
                ),
                description=f"{change.operation} {change.capability_id} 的源码变更已产生证据",
                verification_kind="source_assertion",
                required_evidence=[f"task:{code_task_ids[len(criteria)]}"],
            )
        )
    for index, text in enumerate(objective.acceptance_criteria):
        criteria.append(
            AcceptanceCriterion(
                criterion_id=f"{prefix}:objective-criterion:{index}",
                description=text,
                verification_kind="source_assertion",
                required_evidence=[f"task:{verify_id}"],
            )
        )
    if verification_plan is not None:
        for assertion in verification_plan.source_assertions:
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"{prefix}:source:{assertion.assertion_id}",
                    description=assertion.description,
                    verification_kind="source_assertion",
                    required_evidence=[f"source-assert:{assertion.assertion_id}"],
                )
            )
        for test in verification_plan.component_tests:
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"{prefix}:component-test:{test.test_id}",
                    description=test.description,
                    verification_kind="component_test",
                    required_evidence=[f"component-test:{test.test_id}"],
                )
            )
        if verification_plan.require_build:
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"{prefix}:build",
                    description="ESP-IDF 构建成功并产生工具证据",
                    verification_kind="build",
                    required_evidence=[f"build:{verify_id}"],
                )
            )
        for assertion in verification_plan.firmware_assertions:
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"{prefix}:firmware:{assertion.assertion_id}",
                    description=assertion.description,
                    verification_kind="firmware_resource",
                    required_evidence=[f"firmware-assert:{assertion.assertion_id}"],
                )
            )
        if verification_plan.require_device:
            if verification_plan.device_assertions:
                for assertion in verification_plan.device_assertions:
                    criteria.append(
                        AcceptanceCriterion(
                            criterion_id=f"{prefix}:device:{assertion.assertion_id}",
                            description=assertion.description,
                            verification_kind="device_log",
                            required_evidence=[f"device-assert:{assertion.assertion_id}"],
                        )
                    )
            else:
                criteria.append(
                    AcceptanceCriterion(
                        criterion_id=f"{prefix}:device",
                        description="设备运行日志无致命诊断",
                        verification_kind="device_log",
                        required_evidence=[f"device:{verify_id}"],
                    )
                )
        for probe in verification_plan.protocol_probes:
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"{prefix}:protocol:{probe.probe_id}",
                    description=probe.description,
                    verification_kind="protocol_probe",
                    required_evidence=[f"protocol-probe:{probe.probe_id}"],
                )
            )
        for scenario in verification_plan.runtime_scenarios:
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"{prefix}:runtime:{scenario.scenario_id}",
                    description=scenario.description,
                    verification_kind=scenario.kind,
                    required_evidence=[f"runtime-scenario:{scenario.scenario_id}"],
                )
            )
    if not criteria:
        criteria.append(
            AcceptanceCriterion(
                criterion_id=f"{prefix}:preserve",
                description="既有能力未被本轮变更意外删除",
                verification_kind="source_assertion",
                required_evidence=[f"task:{verify_id}"],
            )
        )

    return AgentTaskGraph(
        objective_id=objective.objective_id,
        revision=objective.revision,
        tasks=tasks,
        acceptance_criteria=criteria,
    )
