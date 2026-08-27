import json

from luxar.adapters.deepseek.agent_code_engineer import (
    DeepSeekAgentCodeEngineer,
)
from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.domain.agent.changes import CapabilityChange, ChangeSet
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.agent.tasks import build_task_graph
from luxar.domain.evidence import BuildDiagnostic, BuildEvidence
from luxar.domain.repairs import ProjectFile


def test_agent_code_engineer_receives_task_scope_hashes_and_preserves() -> None:
    client = FakeJsonCompletionClient([{"bundle_id": "bundle"}])
    engineer = DeepSeekAgentCodeEngineer(client, "deepseek-reasoner")
    objective = ProjectObjective(
        objective_id="obj",
        title="modify MQTT",
        description="modify MQTT topic",
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="modify",
                capability_id="network.mqtt_client",
            ),
            CapabilityChange(
                operation="preserve",
                capability_id="bus.i2c",
            ),
        ]
    )
    files = [
        ProjectFile(
            path="main/main.c",
            content="void app_main(void) {}",
        )
    ]
    graph = build_task_graph(
        objective,
        change_set,
        allowed_paths_by_capability={
            "network.mqtt_client": ["main/main.c"]
        },
    )
    task = next(task for task in graph.tasks if task.kind == "code_change")
    project_model = ProjectModelExtractor().extract(files)

    result = engineer.create_bundle(objective, task, project_model, files)

    system_prompt, user_prompt, model = client.calls[0]
    payload = json.loads(user_prompt)
    assert result == {"bundle_id": "bundle"}
    assert payload["task"]["allowed_paths"] == ["main/main.c"]
    assert payload["task"]["preserves"] == ["bus.i2c"]
    assert len(payload["project_files"][0]["sha256"]) == 64
    assert "不得扩大 allowed_paths" in system_prompt
    assert "不得声称构建" in system_prompt
    assert model == "deepseek-reasoner"


def test_agent_code_engineer_has_one_bounded_schema_repair_prompt() -> None:
    client = FakeJsonCompletionClient([{"bundle_id": "repaired"}])
    engineer = DeepSeekAgentCodeEngineer(client, "deepseek-reasoner")

    result = engineer.repair_schema(
        "ChangeBundle",
        {"bundle_id": 1},
        [{"type": "string_type", "loc": ["bundle_id"]}],
    )

    system_prompt, user_prompt, _ = client.calls[0]
    assert result == {"bundle_id": "repaired"}
    assert "不扩大 allowed_paths" in system_prompt
    assert json.loads(user_prompt)["invalid_payload"] == {"bundle_id": 1}


def test_agent_code_engineer_receives_previous_build_diagnostics() -> None:
    client = FakeJsonCompletionClient([{"bundle_id": "repair-bundle"}])
    engineer = DeepSeekAgentCodeEngineer(client, "deepseek-reasoner")
    objective = ProjectObjective(
        objective_id="obj",
        title="repair build",
        description="repair the failed build",
    )
    files = [
        ProjectFile(
            path="components/ssd1306/CMakeLists.txt",
            content="idf_component_register(REQUIRES driver freertos)",
        )
    ]
    graph = build_task_graph(
        objective,
        ChangeSet(
            changes=[
                CapabilityChange(
                    operation="modify",
                    capability_id="build.reproduce_and_fix",
                )
            ]
        ),
        allowed_paths_by_capability={
            "build.reproduce_and_fix": [
                "components/ssd1306/CMakeLists.txt"
            ]
        },
    )
    task = next(task for task in graph.tasks if task.kind == "code_change")
    project_model = ProjectModelExtractor().extract(files)
    evidence = BuildEvidence(
        success=False,
        command=["idf.py", "build"],
        return_code=2,
        stderr_summary="driver/gpio.h: No such file or directory",
        error_category="source",
        diagnostics=[
            BuildDiagnostic(
                file="components/ssd1306/ssd1306.h",
                line=6,
                column=10,
                severity="error",
                message="driver/gpio.h: No such file or directory",
            )
        ],
    )

    engineer.create_bundle(
        objective,
        task,
        project_model,
        files,
        build_evidence=evidence,
    )

    payload = json.loads(client.calls[0][1])
    assert payload["previous_build_evidence"]["diagnostics"][0] == {
        "file": "components/ssd1306/ssd1306.h",
        "line": 6,
        "column": 10,
        "severity": "error",
        "code": None,
        "message": "driver/gpio.h: No such file or directory",
    }
    assert "previous_build_evidence" in payload


def test_agent_code_engineer_receives_failure_feedback_for_reflection() -> None:
    client = FakeJsonCompletionClient([{"bundle_id": "repair-bundle"}])
    engineer = DeepSeekAgentCodeEngineer(client, "deepseek-reasoner")
    objective = ProjectObjective(
        objective_id="obj",
        title="repair validation",
        description="repair rejected change",
    )
    files = [ProjectFile(path="main/main.c", content="void app_main(void) {}")]
    graph = build_task_graph(
        objective,
        ChangeSet(
            changes=[
                CapabilityChange(
                    operation="modify",
                    capability_id="gpio.output:P32",
                )
            ]
        ),
        allowed_paths_by_capability={"gpio.output:P32": ["main/main.c"]},
    )
    task = next(task for task in graph.tasks if task.kind == "code_change")

    engineer.create_bundle(
        objective,
        task,
        ProjectModelExtractor().extract(files),
        files,
        failure_feedback=["路径 main/other.c 超出允许范围"],
    )

    system_prompt, user_prompt, _ = client.calls[0]
    payload = json.loads(user_prompt)
    assert payload["previous_failure_feedback"] == [
        "路径 main/other.c 超出允许范围"
    ]
    assert "禁止原样重复" in system_prompt
