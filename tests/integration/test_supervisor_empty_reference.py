from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.settings import DeepSeekSettings
from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.application.agent_graph import build_agent_graph
from luxar.bootstrap import build_deepseek_agent_runtime_context
from luxar.domain.agent.verification import VerificationPlan
from luxar.domain.evidence import BuildEvidence


FIXTURE = Path(__file__).parents[1] / "fixtures" / "full_environment_node"


def _fixture_contents() -> dict[str, str]:
    return {
        path.relative_to(FIXTURE).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(FIXTURE.rglob("*"))
        if path.is_file()
    }


def test_production_code_engineer_path_creates_reference_from_empty_directory(
    tmp_path: Path,
) -> None:
    project = tmp_path / "full_environment_node"
    project.mkdir()
    contents = _fixture_contents()
    verification_plan = VerificationPlan(require_build=True)
    objective_id = "stage10-natural-language-reference"
    code_task_id = (
        f"{objective_id}:code:add:project.full_environment_node"
    )
    client = FakeJsonCompletionClient(
        [
            {
                "intent": "change_objective",
                "objective": {
                    "objective_id": objective_id,
                    "title": "从空目录实现完整环境监测节点",
                    "description": (
                        "实现多外设、网络、存储、OTA、FreeRTOS 和诊断参考工程"
                    ),
                    "acceptance_criteria": ["完整工程构建通过"],
                },
                "change_set": {
                    "changes": [
                        {
                            "operation": "add",
                            "capability_id": "project.full_environment_node",
                            "desired_state": {"target": "esp32"},
                            "rationale": "从空目录创建完整参考工程",
                        }
                    ]
                },
                "allowed_paths_by_capability": {
                    "project.full_environment_node": sorted(contents)
                },
                "objective_changed": True,
            },
            {
                "bundle_id": "stage10-empty-reference-bundle",
                "task_id": code_task_id,
                "description": "创建完整 ESP-IDF 环境监测参考工程",
                "allowed_paths": sorted(contents),
                "preserves": [],
                "changes": [
                    {
                        "operation": "create",
                        "path": path,
                        "content": content,
                    }
                    for path, content in sorted(contents.items())
                ],
            }
        ]
    )
    context = build_deepseek_agent_runtime_context(
        project_path=project,
        build_executor=FakeEspIdf(
            [
                BuildEvidence(
                    success=True,
                    command=["idf.py", "build"],
                    return_code=0,
                )
            ]
        ),
        settings=DeepSeekSettings(
            api_key=SecretStr("test-key"),
            repair_model="deepseek-reasoner",
        ),
        client=client,
    )

    result = build_agent_graph().invoke(
        {
            "task_text": (
                "从空目录创建一个 ESP32 完整环境监测节点，包含 I2C SHT30、"
                "SPI 显示、RS485 Modbus、Wi-Fi、MQTT、NVS、OTA、"
                "四个 FreeRTOS 任务和诊断"
            ),
            "source_message_id": "stage10-natural-language",
            "project_files": [],
            "project_name": "full_environment_node",
            "target_chip": "esp32",
            "verification_plan": verification_plan,
            "trace": [],
            "max_steps": 30,
        },
        context=context,
    )

    assert result["status"] == "completed"
    assert result["acceptance_passed"] is True
    assert result["build_verified"] is True
    assert "bundle:stage10-empty-reference-bundle" in result["evidence_ids"]
    assert len(client.calls) == 2
    assert "Project Planner" in client.calls[0][0]
    assert "task.allowed_paths" in client.calls[1][0]
    assert {
        path.relative_to(project).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(project.rglob("*"))
        if path.is_file()
    } == contents
