import json

import pytest
from pydantic import ValidationError

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.planner import DeepSeekPlanner
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.requirements import FirmwareRequirement
from luxar.ports.errors import CapabilityError


def test_planner_converts_ordered_json_steps_to_execution_plan() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "steps": [
                    {
                        "kind": "create_project",
                        "description": "创建 ESP-IDF 工程",
                    },
                    {
                        "kind": "build_project",
                        "description": "构建并验证工程",
                    },
                ]
            }
        ]
    )
    planner = DeepSeekPlanner(client, "deepseek-v4-flash")
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )

    plan = planner.create_plan(requirement)

    assert plan == ExecutionPlan(
        steps=[
            PlanStep(
                kind="create_project",
                description="创建 ESP-IDF 工程",
            ),
            PlanStep(
                kind="build_project",
                description="构建并验证工程",
            ),
        ]
    )
    assert [step.kind for step in plan.steps] == [
        "create_project",
        "build_project",
    ]


def test_planner_sends_requirement_schema_and_selected_model() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "steps": [
                    {
                        "kind": "build_project",
                        "description": "构建工程",
                    }
                ]
            }
        ]
    )
    planner = DeepSeekPlanner(client, "deepseek-v4-flash")
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )

    planner.create_plan(requirement)

    system_prompt, user_prompt, model = client.calls[0]
    assert "JSON Schema" in system_prompt
    assert '"steps"' in system_prompt
    assert '"build_project"' in system_prompt
    assert "不能发明新动作" in system_prompt
    assert json.loads(user_prompt) == {
        "requirement": requirement.model_dump(mode="json")
    }
    assert model == "deepseek-v4-flash"


@pytest.mark.parametrize(
    "payload",
    [
        {"steps": []},
        {
            "steps": [
                {
                    "kind": "delete_system",
                    "description": "不受支持的动作",
                }
            ]
        },
        {
            "steps": [
                {
                    "kind": "build_project",
                }
            ]
        },
    ],
)
def test_planner_normalizes_invalid_plan_schema(
    payload: dict[str, object],
) -> None:
    client = FakeJsonCompletionClient([payload])
    planner = DeepSeekPlanner(client, "deepseek-v4-flash")
    requirement = FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
    )

    with pytest.raises(CapabilityError) as captured:
        planner.create_plan(requirement)

    assert captured.value.category == "invalid_schema"
    assert captured.value.retryable is False
    assert isinstance(captured.value.__cause__, ValidationError)
    assert "delete_system" not in captured.value.message
