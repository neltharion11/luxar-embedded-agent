from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_flasher import FakeFlasher
from luxar.adapters.fake_log_analyst import FakeLogAnalyst
from luxar.adapters.fake_monitor import FakeMonitor
from luxar.adapters.fake_planner import FakePlanner
from luxar.adapters.fake_project_creator import FakeProjectCreator
from luxar.adapters.fake_repair_planner import FakeRepairPlanner
from luxar.adapters.fake_requirement_parser import FakeRequirementParser
from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.adapters.transactional_code_executor import LocalChangeBundleExecutor
from luxar.application.agent_graph import build_agent_graph
from luxar.application.agent_state import AgentRuntimeContext
from luxar.application.context import RuntimeContext
from luxar.application.runner import run_workflow
from luxar.application.runtime_comparison import (
    RuntimeComparisonScenario,
    RuntimeExecutionSnapshot,
    run_runtime_comparison,
)
from luxar.domain.agent.capabilities import ProjectCapabilityExtractor
from luxar.domain.agent.changes import CapabilityChange, ChangeSet
from luxar.domain.agent.code_changes import ChangeBundle, FileChange
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.agent.tasks import build_task_graph
from luxar.domain.agent.verification import VerificationPlan
from luxar.domain.evidence import BuildDiagnostic, BuildEvidence
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.repairs import FileReplacement, ProjectFile, RepairPlan
from luxar.domain.requirements import FirmwareRequirement


FIXTURE = Path(__file__).parents[1] / "fixtures" / "full_environment_node"
TARGET_PATH = "components/network_service/mqtt_service.c"
OLD_TOPIC = "devices/environment/telemetry"
NEW_TOPIC = "devices/environment/v2/telemetry"


def _project_files(root: Path) -> list[ProjectFile]:
    return [
        ProjectFile(
            path=path.relative_to(root).as_posix(),
            content=path.read_text(encoding="utf-8"),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _capability_ids(root: Path) -> list[str]:
    return [
        capability.capability_id
        for capability in ProjectCapabilityExtractor().extract(
            _project_files(root)
        )
    ]


def _build_success() -> BuildEvidence:
    return BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
    )


def test_same_full_project_incremental_goal_compares_real_runtime_graphs(
    tmp_path: Path,
) -> None:
    legacy_project = tmp_path / "legacy" / "full_environment_node"
    supervisor_project = tmp_path / "supervisor" / "full_environment_node"
    shutil.copytree(FIXTURE, legacy_project)
    shutil.copytree(FIXTURE, supervisor_project)

    baseline_ids = set(_capability_ids(FIXTURE))
    preserved_ids = sorted(baseline_ids - {"network.mqtt_client"})
    scenario = RuntimeComparisonScenario(
        scenario_id="stage10-mqtt-topic-runtime-comparison",
        objective="只修改 MQTT 遥测主题并保留完整工程的其他能力",
        allowed_changed_paths=[TARGET_PATH],
        preserved_capability_ids=preserved_ids,
        require_build=True,
        require_hardware=False,
        require_approval=False,
    )

    def run_legacy(_: RuntimeComparisonScenario) -> RuntimeExecutionSnapshot:
        target = legacy_project / Path(TARGET_PATH)
        replacement = target.read_text(encoding="utf-8").replace(
            OLD_TOPIC,
            NEW_TOPIC,
        )
        failed = BuildEvidence(
            success=False,
            command=["idf.py", "build"],
            return_code=1,
            error_category="source",
            diagnostics=[
                BuildDiagnostic(
                    file=TARGET_PATH,
                    line=1,
                    severity="error",
                    message="MQTT topic must be updated",
                )
            ],
        )
        plan = ExecutionPlan(
            steps=[PlanStep(kind="build_project", description="build")]
        )
        context = RuntimeContext(
            requirement_parser=FakeRequirementParser(
                FirmwareRequirement(
                    target="esp32",
                    goal="modify MQTT telemetry topic",
                )
            ),
            planner=FakePlanner(plan),
            espidf=FakeEspIdf([failed, _build_success()]),
            project_path=legacy_project,
            repair_planner=FakeRepairPlanner(
                RepairPlan(
                    diagnosis="update only the MQTT telemetry topic",
                    replacements=[
                        FileReplacement(path=TARGET_PATH, content=replacement)
                    ],
                )
            ),
            workspace=LocalWorkspaceAdapter(),
            project_creator=FakeProjectCreator([]),
            target_chip="esp32",
            flasher=FakeFlasher([]),
            serial_port=None,
            checkpointer=InMemorySaver(),
            monitor=FakeMonitor([]),
            log_analyst=FakeLogAnalyst([]),
            monitor_timeout_seconds=10,
        )
        result = run_workflow(
            initial_state={
                "task_text": scenario.objective,
                "attempts": 0,
                "max_attempts": 3,
                "trace": [],
            },
            context=context,
        )
        return RuntimeExecutionSnapshot(
            state=result.state,
            capability_ids=_capability_ids(legacy_project),
        )

    def run_supervisor(
        _: RuntimeComparisonScenario,
    ) -> RuntimeExecutionSnapshot:
        files = _project_files(supervisor_project)
        model = ProjectModelExtractor().extract(
            files,
            project_name="full_environment_node",
        )
        target = supervisor_project / Path(TARGET_PATH)
        original = target.read_text(encoding="utf-8")
        replacement = original.replace(OLD_TOPIC, NEW_TOPIC)
        objective = ProjectObjective(
            objective_id=scenario.scenario_id,
            title="修改 MQTT 遥测主题",
            description=scenario.objective,
            acceptance_criteria=["MQTT 主题更新且完整工程其他能力保持不变"],
        )
        change_set = ChangeSet(
            changes=[
                CapabilityChange(
                    operation="modify",
                    capability_id="network.mqtt_client",
                    desired_state={"topic": NEW_TOPIC},
                    rationale="更新遥测主题",
                ),
                *[
                    CapabilityChange(
                        operation="preserve",
                        capability_id=capability_id,
                        rationale="同目标运行时对比要求非回归",
                    )
                    for capability_id in preserved_ids
                ],
            ]
        )
        verification_plan = VerificationPlan(require_build=True)
        task_graph = build_task_graph(
            objective,
            change_set,
            current_capability_ids=baseline_ids,
            allowed_paths_by_capability={
                "network.mqtt_client": [TARGET_PATH],
            },
            verification_plan=verification_plan,
        )
        code_task = next(
            task for task in task_graph.tasks if task.kind == "code_change"
        )
        bundle = ChangeBundle(
            bundle_id="stage10-runtime-comparison-mqtt",
            task_id=code_task.task_id,
            description="事务式修改 MQTT 遥测主题",
            allowed_paths=[TARGET_PATH],
            preserves=preserved_ids,
            changes=[
                FileChange(
                    operation="modify",
                    path=TARGET_PATH,
                    content=replacement,
                    expected_sha256=hashlib.sha256(
                        original.encode("utf-8")
                    ).hexdigest(),
                )
            ],
        )
        state = build_agent_graph().invoke(
            {
                "objective": objective,
                "change_set": change_set,
                "capabilities": model.capabilities,
                "project_model": model,
                "hardware_report": model.hardware_report,
                "inspection_complete": True,
                "task_graph": task_graph,
                "verification_plan": verification_plan,
                "change_bundles": {code_task.task_id: bundle},
                "trace": [],
                "max_steps": 30,
            },
            context=AgentRuntimeContext(
                code_executor=LocalChangeBundleExecutor(),
                project_path=supervisor_project,
                build_executor=FakeEspIdf([_build_success()]),
            ),
        )
        return RuntimeExecutionSnapshot(
            state=state,
            capability_ids=_capability_ids(supervisor_project),
        )

    report = run_runtime_comparison(
        scenario,
        legacy_runner=run_legacy,
        supervisor_runner=run_supervisor,
    )

    assert report.supervisor_not_worse is True
    assert report.failed_check_ids == []
    assert report.legacy.changed_files == [TARGET_PATH]
    assert report.supervisor.changed_files == [TARGET_PATH]
    assert report.legacy.build_verified is True
    assert report.supervisor.build_verified is True
    assert report.supervisor.task_depth > report.legacy.task_depth
    assert (
        legacy_project / Path(TARGET_PATH)
    ).read_text(encoding="utf-8").count(NEW_TOPIC) == 1
    assert (
        supervisor_project / Path(TARGET_PATH)
    ).read_text(encoding="utf-8").count(NEW_TOPIC) == 1
