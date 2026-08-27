from pathlib import Path

import pytest

from luxar.domain.agent.acceptance import AcceptanceCriterion, AcceptanceVerifier
from luxar.domain.agent.capabilities import (
    ProjectCapability,
    ProjectCapabilityExtractor,
    PreserveViolationError,
    find_preserve_violations,
)
from luxar.domain.agent.changes import CapabilityChange, ChangeSet, ObjectiveInterpreter
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.schema_repair import (
    SchemaRepairExhausted,
    validate_with_one_repair,
)
from luxar.domain.agent.tasks import build_task_graph, revise_task_graph
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


def test_t2_extractor_finds_existing_gpio13_high() -> None:
    capabilities = ProjectCapabilityExtractor().extract(
        [ProjectFile(path="main/t2.c", content=T2_SOURCE)]
    )

    assert [capability.capability_id for capability in capabilities] == [
        "gpio.output:P13"
    ]
    assert capabilities[0].parameters["level"] == 1
    assert capabilities[0].source_paths == ["main/t2.c"]
    assert capabilities[0].evidence_ids == ["source:main/t2.c:gpio13"]


def test_new_gpio_preserves_existing_gpio() -> None:
    existing = ProjectCapabilityExtractor().extract(
        [ProjectFile(path="main/t2.c", content=T2_SOURCE)]
    )
    interpretation = ObjectiveInterpreter().interpret(
        "新增 P33 高电平",
        existing_capabilities=existing,
        source_message_id="m-2",
    )

    assert interpretation.objective_changed is True
    assert interpretation.change_set is not None
    assert [
        (change.operation, change.capability_id)
        for change in interpretation.change_set.changes
    ] == [
        ("add", "gpio.output:P33"),
        ("preserve", "gpio.output:P13"),
    ]
    assert interpretation.change_set.changes[0].desired_state["level"] == 1


def test_bare_gpio_change_is_ambiguous_when_other_gpio_exists() -> None:
    existing = ProjectCapabilityExtractor().extract(
        [ProjectFile(path="main/t2.c", content=T2_SOURCE)]
    )
    interpretation = ObjectiveInterpreter().interpret(
        "P33 改高",
        existing_capabilities=existing,
    )

    assert interpretation.change_set is not None
    assert interpretation.change_set.unresolved_questions
    assert not any(
        change.operation == "add" and change.capability_id == "gpio.output:P33"
        for change in interpretation.change_set.changes
    )
    assert interpretation.objective_changed is False


def test_modify_remove_and_preserve_are_parsed_per_pin() -> None:
    existing = [
        ProjectCapability(
            capability_id="gpio.output:P13",
            kind="gpio.output",
            parameters={"pin": 13, "level": 0},
        ),
        ProjectCapability(
            capability_id="gpio.output:P33",
            kind="gpio.output",
            parameters={"pin": 33, "level": 1},
        ),
    ]
    modified = ObjectiveInterpreter().interpret(
        "将 P13 从低电平修改为高电平",
        existing_capabilities=existing,
    )
    removed = ObjectiveInterpreter().interpret(
        "删除 P13，但保留 P33",
        existing_capabilities=existing,
    )

    assert [(item.operation, item.capability_id) for item in modified.change_set.changes] == [
        ("modify", "gpio.output:P13"),
        ("preserve", "gpio.output:P33"),
    ]
    assert modified.change_set.changes[0].desired_state["level"] == 1
    assert [(item.operation, item.capability_id) for item in removed.change_set.changes] == [
        ("remove", "gpio.output:P13"),
        ("preserve", "gpio.output:P33"),
    ]


def test_question_does_not_change_existing_objective() -> None:
    objective = ProjectObjective(
        objective_id="obj-1",
        title="blink",
        description="keep the LED behavior",
        source_message_ids=["m-1"],
    )
    interpretation = ObjectiveInterpreter().interpret(
        "GPIO 输出模式是什么？",
        current_objective=objective,
        source_message_id="m-2",
    )

    assert interpretation.intent == "ask_question"
    assert interpretation.objective == objective
    assert interpretation.objective_changed is False
    assert interpretation.change_set is None


def test_status_indicator_goal_is_not_misclassified_as_status_query() -> None:
    interpreter = ObjectiveInterpreter()

    change = interpreter.interpret("实现一个高电平状态指示灯功能")
    status = interpreter.interpret("查看当前项目状态")

    assert change.intent == "change_objective"
    assert change.objective_changed is True
    assert status.intent == "inspect_status"
    assert status.objective_changed is False


def test_task_graph_contains_layered_tasks_and_preserve_contract() -> None:
    interpretation = ObjectiveInterpreter().interpret("新增 P33 高电平")
    assert interpretation.objective is not None
    assert interpretation.change_set is not None
    graph = build_task_graph(interpretation.objective, interpretation.change_set)

    assert [task.kind for task in graph.tasks] == [
        "inspect_project",
        "architecture_plan",
        "code_change",
        "verify_acceptance",
    ]
    code_task = next(task for task in graph.tasks if task.kind == "code_change")
    assert code_task.preserves == []
    assert graph.ready_tasks()[0].kind == "inspect_project"


def test_preserve_violation_is_deterministic() -> None:
    before = [
        ProjectCapability(
            capability_id="gpio.output:P13",
            kind="gpio.output",
            parameters={"pin": 13},
        )
    ]
    after: list[ProjectCapability] = []

    assert find_preserve_violations(
        ["gpio.output:P13"], before, after
    ) == ["gpio.output:P13"]
    with pytest.raises(PreserveViolationError, match="gpio.output:P13"):
        from luxar.domain.agent.capabilities import assert_preserved

        assert_preserved(["gpio.output:P13"], before, after)


def test_acceptance_verifier_requires_real_evidence_ids() -> None:
    criterion = AcceptanceCriterion(
        criterion_id="c1",
        description="source changed",
        verification_kind="source_assertion",
        required_evidence=["task:code-1"],
    )
    pending = AcceptanceVerifier().verify([criterion], [])
    passed = AcceptanceVerifier().verify([criterion], ["task:code-1"])

    assert pending.all_passed is False
    assert pending.criteria[0].status == "pending"
    assert passed.all_passed is True
    assert passed.criteria[0].evidence_ids == ["task:code-1"]


def test_schema_output_is_repaired_once() -> None:
    repaired = validate_with_one_repair(
        ProjectObjective,
        {"objective_id": "obj-1", "title": "x"},
        lambda payload, errors: {
            **payload,
            "description": "repaired",
        },
    )

    assert repaired.description == "repaired"

    with pytest.raises(SchemaRepairExhausted):
        validate_with_one_repair(
            ProjectObjective,
            {"objective_id": "obj-1", "title": "x"},
            lambda payload, errors: payload,
        )


def test_plan_revision_keeps_unaffected_task_state_and_reports_diff() -> None:
    first_objective = ProjectObjective(
        objective_id="obj-revision",
        title="environment",
        description="add P33",
        revision=1,
    )
    first_changes = ChangeSet(
        changes=[
            CapabilityChange(
                operation="add",
                capability_id="gpio.output:P33",
                desired_state={"pin": 33, "level": 1},
            )
        ]
    )
    previous = build_task_graph(first_objective, first_changes)
    p33_task = next(task for task in previous.tasks if task.kind == "code_change")
    previous = previous.update_task(p33_task.task_id, status="passed", attempts=1)

    second_objective = first_objective.model_copy(
        update={"description": "add P33 and MQTT", "revision": 2}
    )
    second_changes = ChangeSet(
        changes=[
            *first_changes.changes,
            CapabilityChange(
                operation="add",
                capability_id="network.mqtt_client",
                desired_state={"topic": "telemetry"},
            ),
        ]
    )
    revision = revise_task_graph(previous, second_objective, second_changes)

    preserved_p33 = next(
        task for task in revision.graph.tasks if task.task_id == p33_task.task_id
    )
    assert preserved_p33.status == "passed"
    assert preserved_p33.attempts == 1
    assert any(
        task_id.endswith("network.mqtt_client")
        for task_id in revision.diff.added_task_ids
    )
    assert p33_task.task_id in revision.diff.preserved_task_ids
    assert revision.graph.revision == 2
