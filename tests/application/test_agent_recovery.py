from __future__ import annotations

from pathlib import Path

from luxar.application.agent_graph import build_agent_graph
from luxar.application.agent_state import AgentRuntimeContext
from luxar.domain.agent.changes import CapabilityChange, ChangeSet
from luxar.domain.agent.code_changes import (
    ChangeBundle,
    ChangeBundleError,
    ChangeBundleValidation,
)
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.repairs import ProjectFile
from luxar.domain.agent.tasks import build_task_graph


class _Executor:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def execute(
        self,
        project_path: Path,
        bundle: ChangeBundle,
    ) -> ChangeBundleValidation:
        del project_path, bundle
        self.calls += 1
        if self.calls <= self.failures:
            raise ChangeBundleError(
                "semantic",
                "模拟语义校验失败",
                ["interface-mismatch"],
            )
        return ChangeBundleValidation(
            before_fingerprint="a" * 64,
            after_fingerprint="b" * 64,
            changed_files=["main.c"],
            diff_summary=["create: main.c"],
        )


class _ChangingFailureExecutor(_Executor):
    def execute(
        self,
        project_path: Path,
        bundle: ChangeBundle,
    ) -> ChangeBundleValidation:
        del project_path, bundle
        self.calls += 1
        raise ChangeBundleError(
            "semantic",
            "模拟每次不同的语义校验失败",
            [f"interface-mismatch-{self.calls}"],
        )


def _state_for_code_task() -> tuple[dict[str, object], str]:
    objective = ProjectObjective(
        objective_id="recovery-objective",
        title="recovery",
        description="test recovery",
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="add",
                capability_id="gpio.output:P33",
                desired_state={"pin": 33, "level": 1},
            )
        ]
    )
    graph = build_task_graph(objective, change_set)
    inspect = next(task for task in graph.tasks if task.kind == "inspect_project")
    architecture = next(
        task for task in graph.tasks if task.kind == "architecture_plan"
    )
    graph = graph.update_task(inspect.task_id, status="passed")
    graph = graph.update_task(architecture.task_id, status="passed")
    code_task = next(task for task in graph.tasks if task.kind == "code_change")
    bundle = ChangeBundle(
        bundle_id="recovery-bundle",
        task_id=code_task.task_id,
        description="recovery test",
        allowed_paths=["main.c"],
        changes=[
            {
                "operation": "create",
                "path": "main.c",
                "content": "void app_main(void) {}\n",
            }
        ],
    )
    return (
        {
            "objective": objective,
            "change_set": change_set,
            "inspection_complete": True,
            "task_graph": graph,
            "change_bundles": {code_task.task_id: bundle},
            "trace": [],
            "max_steps": 20,
        },
        code_task.task_id,
    )


def test_schema_repair_is_used_once_and_recorded() -> None:
    state, task_id = _state_for_code_task()
    raw_bundle = state["change_bundles"][task_id]
    assert isinstance(raw_bundle, ChangeBundle)
    state["change_bundles"] = {
        task_id: {
            **raw_bundle.model_dump(mode="json"),
            "changes": [
                {
                    "operation": "create",
                    "path": "main.c",
                }
            ],
        }
    }
    repair_calls: list[str] = []

    def repair(
        model_name: str,
        payload: object,
        errors: list[dict[str, object]],
    ) -> object:
        repair_calls.append(model_name)
        assert errors
        assert isinstance(payload, dict)
        return {
            **payload,
            "changes": [
                {
                    "operation": "create",
                    "path": "main.c",
                    "content": "void app_main(void) {}\n",
                }
            ],
        }

    executor = _Executor(failures=0)
    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            code_executor=executor,
            project_path=Path("F:/LUXAR"),
            schema_repair=repair,
        ),
    )

    assert result["status"] == "completed"
    assert repair_calls == ["ChangeBundle"]
    assert result["failure_history"] == []
    assert result["schema_repairs"][0]["task_id"] == task_id
    assert result["schema_repairs"][0]["errors"]


def test_first_semantic_failure_is_retried_and_can_succeed() -> None:
    state, task_id = _state_for_code_task()
    executor = _Executor(failures=1)

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            code_executor=executor,
            project_path=Path("F:/LUXAR"),
        ),
    )

    assert result["status"] == "completed"
    assert executor.calls == 2
    assert result["task_graph"].tasks[2].task_id == task_id
    assert result["task_graph"].tasks[2].attempts == 2
    assert len(result["failure_history"]) == 1
    assert result["failure_history"][0].category == "semantic"
    assert result["task_feedback"][task_id]


def test_semantic_failure_feedback_is_sent_to_regenerated_bundle() -> None:
    state, task_id = _state_for_code_task()
    state["change_bundles"] = {}
    files = [ProjectFile(path="main.c", content="void app_main(void) {}\n")]
    state["project_files"] = files
    state["project_model"] = ProjectModelExtractor().extract(files)

    class Engineer:
        def __init__(self) -> None:
            self.feedback: list[list[str]] = []

        def create_bundle(
            self,
            objective,
            task,
            project_model,
            project_files,
            build_evidence=None,
            failure_feedback=None,
        ):
            del objective, project_model, project_files, build_evidence
            self.feedback.append(list(failure_feedback or []))
            return {
                "bundle_id": f"regenerated-{len(self.feedback)}",
                "task_id": task.task_id,
                "description": "regenerated after validation feedback",
                "allowed_paths": ["main.c"],
                "changes": [
                    {
                        "operation": "modify",
                        "path": "main.c",
                        "content": "void app_main(void) { /* fixed */ }\n",
                    }
                ],
            }

    engineer = Engineer()
    executor = _Executor(failures=1)
    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            code_engineer=engineer,
            code_executor=executor,
            project_path=Path("F:/LUXAR"),
        ),
    )

    assert result["status"] == "completed"
    assert len(engineer.feedback) == 2
    assert engineer.feedback[0] == []
    assert any("interface-mismatch" in item for item in engineer.feedback[1])


def test_repeated_semantic_failure_blocks_only_the_task() -> None:
    state, task_id = _state_for_code_task()
    executor = _Executor(failures=5)

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            code_executor=executor,
            project_path=Path("F:/LUXAR"),
        ),
    )

    blocked_task = next(
        task for task in result["task_graph"].tasks if task.task_id == task_id
    )
    assert result["status"] == "blocked"
    assert executor.calls == 2
    assert blocked_task.status == "blocked"
    assert len(result["failure_history"]) == 2
    assert result["failure_history"][1].repeated is True
    assert "当前任务阻塞" in result["last_error"]


def test_failure_budget_blocks_task_after_different_errors() -> None:
    state, task_id = _state_for_code_task()
    executor = _ChangingFailureExecutor(failures=5)

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            code_executor=executor,
            project_path=Path("F:/LUXAR"),
        ),
    )

    blocked_task = next(
        task for task in result["task_graph"].tasks if task.task_id == task_id
    )
    assert result["status"] == "blocked"
    assert executor.calls == 2
    assert blocked_task.status == "blocked"
    assert result["failure_history"][1].repeated is False
