import json

import pytest

from luxar.adapters.deepseek.agent_planner import DeepSeekAgentPlanner
from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.repairs import ProjectFile
from luxar.ports.errors import CapabilityError


def _valid_plan() -> dict[str, object]:
    return {
        "intent": "change_objective",
        "objective": {
            "objective_id": "environment-node",
            "title": "完整环境监测节点",
            "description": "创建完整 ESP-IDF 环境监测工程",
            "acceptance_criteria": ["真实构建通过"],
        },
        "change_set": {
            "changes": [
                {
                    "operation": "add",
                    "capability_id": "project.full_environment_node",
                    "desired_state": {"target": "esp32"},
                }
            ]
        },
        "allowed_paths_by_capability": {
            "project.full_environment_node": [
                "CMakeLists.txt",
                "main/main.c",
            ]
        },
        "objective_changed": True,
    }


def test_agent_planner_generates_actionable_scoped_interpretation() -> None:
    client = FakeJsonCompletionClient([_valid_plan()])
    planner = DeepSeekAgentPlanner(client, "deepseek-chat")
    model = ProjectModelExtractor().extract(
        [],
        project_name="environment-node",
        target_chip="esp32",
    )

    interpretation = planner.interpret_goal(
        "从空目录创建完整环境监测节点",
        model,
    )

    assert interpretation.objective is not None
    assert interpretation.change_set is not None
    assert interpretation.allowed_paths_by_capability[
        "project.full_environment_node"
    ] == ["CMakeLists.txt", "main/main.c"]
    system_prompt, user_prompt, selected_model = client.calls[0]
    assert "allowed_paths_by_capability" in system_prompt
    assert json.loads(user_prompt)["task_text"] == "从空目录创建完整环境监测节点"
    assert selected_model == "deepseek-chat"


def test_agent_planner_repairs_invalid_schema_once() -> None:
    client = FakeJsonCompletionClient(
        [
            {"intent": "change_objective", "objective": "invalid"},
            _valid_plan(),
        ]
    )
    planner = DeepSeekAgentPlanner(client, "deepseek-chat")

    interpretation = planner.interpret_goal(
        "创建环境节点",
        ProjectModelExtractor().extract([]),
    )

    assert interpretation.objective is not None
    assert len(client.calls) == 2
    assert "修复" in client.calls[1][0]


def test_agent_planner_rejects_dropped_existing_capability() -> None:
    response = _valid_plan()
    client = FakeJsonCompletionClient([response])
    planner = DeepSeekAgentPlanner(client, "deepseek-chat")
    model = ProjectModelExtractor().extract(
        [
            ProjectFile(
                path="main/main.c",
                content=(
                    "gpio_set_direction(GPIO_NUM_13, GPIO_MODE_OUTPUT);\n"
                    "gpio_set_level(GPIO_NUM_13, 1);"
                ),
            )
        ]
    )

    with pytest.raises(CapabilityError) as captured:
        planner.interpret_goal("扩展环境节点", model)

    assert captured.value.category == "invalid_schema"


@pytest.mark.parametrize(
    "unsafe_path",
    [
        ".git/config",
        ".luxar/state.json",
        "build/generated.c",
        "managed_components/vendor/component.c",
    ],
)
def test_agent_planner_rejects_excluded_project_roots(
    unsafe_path: str,
) -> None:
    response = _valid_plan()
    response["allowed_paths_by_capability"] = {
        "project.full_environment_node": [unsafe_path]
    }
    planner = DeepSeekAgentPlanner(
        FakeJsonCompletionClient([response]),
        "deepseek-chat",
    )

    with pytest.raises(CapabilityError) as captured:
        planner.interpret_goal(
            "创建环境节点",
            ProjectModelExtractor().extract([]),
        )

    assert captured.value.category == "invalid_schema"
