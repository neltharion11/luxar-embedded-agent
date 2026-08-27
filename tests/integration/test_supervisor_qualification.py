from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from luxar.adapters.transactional_code_executor import LocalChangeBundleExecutor
from luxar.application.agent_graph import build_agent_graph
from luxar.application.agent_state import AgentRuntimeContext
from luxar.domain.agent.capabilities import ProjectCapabilityExtractor
from luxar.domain.agent.changes import CapabilityChange, ChangeSet
from luxar.domain.agent.code_changes import (
    ChangeBundle,
    ChangeBundleError,
    FileChange,
)
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.agent.tasks import build_task_graph
from luxar.domain.repairs import ProjectFile


FIXTURE = Path(__file__).parents[1] / "fixtures" / "full_environment_node"


def _project_files(root: Path) -> list[ProjectFile]:
    return [
        ProjectFile(
            path=path.relative_to(root).as_posix(),
            content=path.read_text(encoding="utf-8"),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_environment_reference_model_covers_stage10_subsystems() -> None:
    files = _project_files(FIXTURE)

    model = ProjectModelExtractor().extract(
        files,
        project_name="full_environment_node",
    )

    component_ids = {
        component.component_id for component in model.component_graph.components
    }
    assert {
        "main",
        "env_sensor",
        "display",
        "modbus_service",
        "network_service",
        "storage_service",
        "ota_service",
        "diagnostics",
    } <= component_ids
    assert model.target_chip == "esp32"
    assert {entry["name"] for entry in model.configuration.partition_entries} >= {
        "nvs",
        "otadata",
        "ota_0",
        "ota_1",
    }
    assert {
        "bus.i2c",
        "bus.spi",
        "bus.uart",
        "network.wifi",
        "network.mqtt_client",
        "protocol.modbus_rtu",
        "storage.nvs_config",
        "system.ota",
        "sync.queue",
        "task.freertos:acquisition_task",
        "task.freertos:display_task",
        "task.freertos:upload_task",
        "task.freertos:command_task",
    } <= {capability.capability_id for capability in model.capabilities}
    assert {package.protocol_id for package in model.hardware_report.protocols} >= {
        "i2c",
        "spi",
        "uart",
        "wifi",
        "mqtt",
        "modbus",
    }
    assert model.hardware_report.has_blocking_issue is False
    assert model.resource_graph.has_blocking_conflict is False
    flow_nodes = {
        node.node_id for flow in model.data_flows for node in flow.nodes
    }
    assert {
        "sensor.sht30",
        "bus.i2c",
        "display",
        "network:mqtt",
        "storage:nvs",
        "queue:data",
    } <= flow_nodes


def test_supervisor_incremental_mqtt_change_preserves_unrelated_capabilities(
    tmp_path: Path,
) -> None:
    project = tmp_path / "full_environment_node"
    shutil.copytree(FIXTURE, project)
    files = _project_files(project)
    model = ProjectModelExtractor().extract(
        files,
        project_name="full_environment_node",
    )
    baseline = ProjectCapabilityExtractor().extract(files)
    baseline_ids = {capability.capability_id for capability in baseline}
    assert {
        "gpio.output:P13",
        "bus.i2c",
        "bus.spi",
        "bus.uart",
        "network.wifi",
        "network.mqtt_client",
        "protocol.modbus_rtu",
        "storage.nvs_config",
        "system.ota",
        "task.freertos:acquisition_task",
    } <= baseline_ids

    target_path = "components/network_service/mqtt_service.c"
    target = project / Path(target_path)
    original = target.read_text(encoding="utf-8")
    replacement = original.replace(
        "devices/environment/telemetry",
        "devices/environment/v2/telemetry",
    )
    preserved_ids = sorted(baseline_ids - {"network.mqtt_client"})
    objective = ProjectObjective(
        objective_id="stage10-mqtt-topic",
        title="修改 MQTT 遥测主题",
        description="只修改 MQTT 遥测主题并保留传感器、显示、Modbus、OTA 和任务能力",
        acceptance_criteria=["MQTT 主题更新且现有能力保持不变"],
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="modify",
                capability_id="network.mqtt_client",
                desired_state={"topic": "devices/environment/v2/telemetry"},
                rationale="更新遥测主题",
            ),
            *[
                CapabilityChange(
                    operation="preserve",
                    capability_id=capability_id,
                    rationale="完整工程增量修改不得破坏无关能力",
                )
                for capability_id in preserved_ids
            ],
        ]
    )
    task_graph = build_task_graph(
        objective,
        change_set,
        current_capability_ids=baseline_ids,
        allowed_paths_by_capability={
            "network.mqtt_client": [target_path],
        },
    )
    code_task = next(
        task for task in task_graph.tasks if task.kind == "code_change"
    )
    bundle = ChangeBundle(
        bundle_id="stage10-mqtt-topic-bundle",
        task_id=code_task.task_id,
        description="事务式修改 MQTT 遥测主题",
        allowed_paths=[target_path],
        preserves=preserved_ids,
        changes=[
            FileChange(
                operation="modify",
                path=target_path,
                content=replacement,
                expected_sha256=hashlib.sha256(
                    original.encode("utf-8")
                ).hexdigest(),
            )
        ],
    )
    before_hashes = {
        item.path: _sha256(project / Path(item.path)) for item in files
    }

    result = build_agent_graph().invoke(
        {
            "objective": objective,
            "change_set": change_set,
            "capabilities": model.capabilities,
            "project_model": model,
            "hardware_report": model.hardware_report,
            "inspection_complete": True,
            "task_graph": task_graph,
            "change_bundles": {code_task.task_id: bundle},
            "trace": [],
            "max_steps": 30,
        },
        context=AgentRuntimeContext(
            code_executor=LocalChangeBundleExecutor(),
            project_path=project,
        ),
    )

    assert result["status"] == "completed"
    assert result["acceptance_passed"] is True
    assert target.read_text(encoding="utf-8") == replacement
    assert "bundle:stage10-mqtt-topic-bundle" in result["evidence_ids"]
    validation = result["change_validations"][code_task.task_id]
    assert set(preserved_ids) <= {
        capability.capability_id
        for capability in validation.after_capabilities
    }
    for relative_path, digest in before_hashes.items():
        if relative_path != target_path:
            assert _sha256(project / Path(relative_path)) == digest


def test_full_project_bundle_cannot_remove_preserved_i2c_capability(
    tmp_path: Path,
) -> None:
    project = tmp_path / "full_environment_node"
    shutil.copytree(FIXTURE, project)
    target_path = "components/env_sensor/sht30.c"
    target = project / Path(target_path)
    bundle = ChangeBundle(
        bundle_id="stage10-remove-sensor",
        task_id="stage10:code:remove-sensor",
        description="模拟错误补丁删除传感器驱动",
        allowed_paths=[target_path],
        preserves=["bus.i2c"],
        changes=[
            FileChange(
                operation="delete",
                path=target_path,
                expected_sha256=_sha256(target),
            )
        ],
    )

    with pytest.raises(ChangeBundleError) as captured:
        LocalChangeBundleExecutor().execute(project, bundle)

    assert captured.value.category == "preserve_violation"
    assert captured.value.details == ("bus.i2c",)
    assert target.is_file()
