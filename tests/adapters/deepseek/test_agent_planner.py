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


@pytest.mark.parametrize(
    "constraint",
    [
        "只允许修改 main/CMakeLists.txt 文件内容",
        "只能修改 main/pdf1.c",
        "禁止修改 components/ssd1306/ssd1306.c 之外的任何文件",
    ],
)
def test_agent_planner_rejects_file_path_lock_constraints(
    constraint: str,
) -> None:
    """路径锁约束会把验证驱动的修复锁死，规划时必须拒绝。"""

    response = _valid_plan()
    response["objective"]["constraints"] = [constraint]
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
    assert "path lock" in captured.value.message


def test_agent_planner_widens_scope_with_capability_source_files() -> None:
    """allowed_paths 下限必须是能力的全部实现文件，而不是本轮变更包的文件。"""

    model = ProjectModelExtractor().extract(
        [
            ProjectFile(path="main/i2c.c", content="i2c_master_start()"),
            ProjectFile(path="main/i2c_extra.c", content="i2c_param_config()"),
        ]
    )
    response = {
        "intent": "change_objective",
        "objective": {
            "objective_id": "i2c-fix",
            "title": "修复 I2C 总线",
            "description": "修复 I2C 总线实现",
        },
        "change_set": {
            "changes": [
                {
                    "operation": "modify",
                    "capability_id": "bus.i2c",
                    "desired_state": {"speed": 100000},
                }
            ]
        },
        "allowed_paths_by_capability": {"bus.i2c": ["main/i2c.c"]},
        "objective_changed": True,
    }
    planner = DeepSeekAgentPlanner(
        FakeJsonCompletionClient([response]),
        "deepseek-chat",
    )

    interpretation = planner.interpret_goal("修复 I2C 总线", model)

    assert interpretation.allowed_paths_by_capability["bus.i2c"] == [
        "main/i2c.c",
        "main/i2c_extra.c",
    ]
