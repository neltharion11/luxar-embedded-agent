import hashlib

from langgraph.checkpoint.memory import InMemorySaver

from luxar.application.agent_graph import build_agent_graph
from luxar.application.agent_state import AgentRuntimeContext
from luxar.adapters.transactional_code_executor import LocalChangeBundleExecutor
from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.domain.agent.capabilities import ProjectCapabilityExtractor
from luxar.domain.agent.changes import ObjectiveInterpreter
from luxar.domain.agent.code_changes import ChangeBundle, FileChange
from luxar.domain.agent.tasks import build_task_graph
from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.repairs import ProjectFile


T2_SOURCE = """
#include \"driver/gpio.h\"
void app_main(void) {
    gpio_config_t io_conf = {};
    io_conf.mode = GPIO_MODE_OUTPUT;
    io_conf.pin_bit_mask = (1ULL << GPIO_NUM_13);
    gpio_config(&io_conf);
    gpio_set_level(GPIO_NUM_13, 1);
}
"""


def test_supervisor_without_code_executor_never_claims_completion() -> None:
    result = build_agent_graph().invoke(
        {
            "task_text": "新增 P33 高电平",
            "source_message_id": "m-2",
            "project_files": [{"path": "main/t2.c", "content": T2_SOURCE}],
            "trace": [],
            "max_steps": 20,
        }
    )

    assert result["status"] == "blocked"
    assert result.get("acceptance_passed", False) is False
    assert "gpio.output:P13" in {
        capability.capability_id for capability in result["capabilities"]
    }
    assert result["project_model"].resource_graph.allocations[0].resource_kind == "pin"
    assert result["project_model"].configuration.cmake_paths == []
    assert [task.kind for task in result["task_graph"].tasks] == [
        "inspect_project",
        "architecture_plan",
        "code_change",
        "verify_acceptance",
    ]
    assert result["trace"][0:3] == [
        "load_project_session",
        "supervisor",
        "project_inspector",
    ]
    assert "acceptance_verifier" not in result["trace"]
    assert "代码执行器" in result["last_error"]


def test_supervisor_generic_goal_without_actionable_change_awaits_inputs() -> None:
    result = build_agent_graph().invoke(
        {
            "task_text": "实现完整环境监测节点",
            "source_message_id": "stage10-generic-goal",
            "project_files": [],
            "trace": [],
            "max_steps": 20,
        }
    )

    assert result["status"] == "awaiting_user"
    assert "task_graph" not in result
    assert "可执行变更" in result["last_error"]
    assert "complete_objective" not in result["trace"]


def test_supervisor_answers_question_without_creating_or_changing_objective() -> None:
    result = build_agent_graph().invoke(
        {
            "task_text": "GPIO 输出模式是什么？",
            "project_files": [],
            "trace": [],
        }
    )

    assert result["status"] == "awaiting_user"
    assert "objective" not in result
    assert result["interpretation"].objective_changed is False


def test_supervisor_stops_on_step_budget_and_preserves_checkpoint_state() -> None:
    result = build_agent_graph(checkpointer=InMemorySaver()).invoke(
        {
            "task_text": "新增 P33 高电平",
            "project_files": [],
            "trace": [],
            "max_steps": 1,
        },
        config={"configurable": {"thread_id": "agent-budget-1"}},
    )

    assert result["status"] == "failed"
    assert result["step_count"] == 2
    assert result["decision"].action == "fail_objective"


def test_supervisor_code_change_uses_transactional_executor(tmp_path) -> None:
    source = T2_SOURCE
    source_file = tmp_path / "main" / "t2.c"
    source_file.parent.mkdir()
    source_file.write_bytes(source.encode("utf-8"))
    project_files = [ProjectFile(path="main/t2.c", content=source)]
    capabilities = ProjectCapabilityExtractor().extract(project_files)
    interpretation = ObjectiveInterpreter().interpret(
        "新增 P33 高电平",
        existing_capabilities=capabilities,
        source_message_id="m-executor",
    )
    assert interpretation.objective is not None
    assert interpretation.change_set is not None
    task_graph = build_task_graph(
        interpretation.objective,
        interpretation.change_set,
        allowed_paths_by_capability={"gpio.output:P33": ["main/t2.c"]},
    )
    inspect_task = next(
        task for task in task_graph.tasks if task.kind == "inspect_project"
    )
    architecture_task = next(
        task for task in task_graph.tasks if task.kind == "architecture_plan"
    )
    code_task = next(
        task for task in task_graph.tasks if task.kind == "code_change"
    )
    task_graph = task_graph.update_task(inspect_task.task_id, status="passed")
    task_graph = task_graph.update_task(architecture_task.task_id, status="passed")
    updated_source = source.replace(
        "gpio_set_level(GPIO_NUM_13, 1);",
        "gpio_set_level(GPIO_NUM_13, 1);\n"
        "    gpio_set_level(GPIO_NUM_33, 1);",
    )
    bundle = ChangeBundle(
        bundle_id="bundle-supervisor-1",
        task_id=code_task.task_id,
        description="为 t2 增加 P33 高电平输出",
        allowed_paths=["main/t2.c"],
        preserves=["gpio.output:P13"],
        changes=[
            FileChange(
                operation="modify",
                path="main/t2.c",
                content=updated_source,
                expected_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
            )
        ],
    )

    result = build_agent_graph().invoke(
        {
            "objective": interpretation.objective,
            "change_set": interpretation.change_set,
            "capabilities": capabilities,
            "project_files": project_files,
            "inspection_complete": True,
            "task_graph": task_graph,
            "change_bundles": {code_task.task_id: bundle},
            "trace": [],
            "max_steps": 20,
        },
        context=AgentRuntimeContext(
            code_executor=LocalChangeBundleExecutor(),
            project_path=tmp_path,
        ),
    )

    assert result["status"] == "completed"
    assert result["acceptance_passed"] is True
    assert "bundle:bundle-supervisor-1" in result["evidence_ids"]
    assert "GPIO_NUM_33" in source_file.read_text(encoding="utf-8")


def test_supervisor_requests_missing_bundle_from_code_engineer(tmp_path) -> None:
    source_file = tmp_path / "main" / "t2.c"
    source_file.parent.mkdir()
    source_file.write_bytes(T2_SOURCE.encode("utf-8"))
    files = [ProjectFile(path="main/t2.c", content=T2_SOURCE)]
    capabilities = ProjectCapabilityExtractor().extract(files)
    interpretation = ObjectiveInterpreter().interpret(
        "新增 P33 高电平",
        existing_capabilities=capabilities,
        source_message_id="m-engineer",
    )
    assert interpretation.objective is not None
    assert interpretation.change_set is not None
    task_graph = build_task_graph(
        interpretation.objective,
        interpretation.change_set,
        allowed_paths_by_capability={"gpio.output:P33": ["main/t2.c"]},
    )
    code_task = next(
        task for task in task_graph.tasks if task.kind == "code_change"
    )
    updated = T2_SOURCE.replace(
        "gpio_set_level(GPIO_NUM_13, 1);",
        "gpio_set_level(GPIO_NUM_13, 1);\n"
        "    gpio_set_level(GPIO_NUM_33, 1);",
    )

    class Engineer:
        calls = 0

        def create_bundle(
            self,
            objective,
            task,
            project_model,
            project_files,
            build_evidence=None,
            failure_feedback=None,
            reuse_candidates=None,
        ):
            del build_evidence, failure_feedback, reuse_candidates
            self.calls += 1
            assert task.task_id == code_task.task_id
            # FakeWorkspace 会为每个文件填充 sha256；这里只比较 path/content
            assert [f.path for f in project_files] == [f.path for f in files]
            assert [f.content for f in project_files] == [f.content for f in files]
            return {
                "bundle_id": "generated-bundle",
                "task_id": task.task_id,
                "description": "generated scoped change",
                "allowed_paths": ["main/t2.c"],
                "preserves": [],
                "changes": [
                    {
                        "operation": "modify",
                        "path": "main/t2.c",
                        "content": updated,
                        "expected_sha256": hashlib.sha256(
                            T2_SOURCE.encode("utf-8")
                        ).hexdigest(),
                    }
                ],
            }

    engineer = Engineer()

    class RecordingExecutor(LocalChangeBundleExecutor):
        seen_bundle = None

        def execute(self, project_path, bundle):
            self.seen_bundle = bundle
            return super().execute(project_path, bundle)

    executor = RecordingExecutor()
    model = ProjectModelExtractor().extract(files)
    result = build_agent_graph().invoke(
        {
            "objective": interpretation.objective,
            "change_set": interpretation.change_set,
            "capabilities": capabilities,
            "project_files": files,
            "project_model": model,
            "hardware_report": model.hardware_report,
            "inspection_complete": True,
            "task_graph": task_graph,
            "trace": [],
            "max_steps": 20,
        },
        context=AgentRuntimeContext(
            code_executor=executor,
            code_engineer=engineer,
            workspace=LocalWorkspaceAdapter(),
            project_path=tmp_path,
        ),
    )

    assert result["status"] == "completed"
    assert engineer.calls == 1
    assert executor.seen_bundle is not None
    assert "gpio.output:P13" in executor.seen_bundle.preserves
    assert "bundle:generated-bundle" in result["evidence_ids"]
    assert "GPIO_NUM_33" in source_file.read_text(encoding="utf-8")


def test_supervisor_blocks_before_planning_for_gpio34_output() -> None:
    result = build_agent_graph().invoke(
        {
            "task_text": "新增 GPIO34 高电平输出",
            "project_files": [
                {
                    "path": "main/main.c",
                    "content": (
                        "gpio_config_t config = {0};\n"
                        "config.pin_bit_mask = 1ULL << GPIO_NUM_34;\n"
                        "config.mode = GPIO_MODE_OUTPUT;\n"
                    ),
                }
            ],
            "trace": [],
            "max_steps": 20,
        }
    )

    assert result["status"] == "blocked"
    assert result["hardware_blocked"] is True
    assert "GPIO34" in result["last_error"]
    assert "hardware_validator" in result["trace"]
    assert "degrade_capability" in result["trace"]
    assert "task_graph" not in result
