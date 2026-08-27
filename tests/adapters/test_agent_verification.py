from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from luxar.adapters.agent_verification import (
    EspIdfArtifactInspectorAdapter,
    LocalComponentTestAdapter,
    RegisteredProtocolProbeAdapter,
    RegisteredRuntimeScenarioAdapter,
)
from luxar.domain.agent.runtime_verification import (
    ProtocolProbeEvidence,
    ProtocolProbeSpec,
    RuntimeScenarioEvidence,
    RuntimeScenarioSpec,
)
from luxar.domain.agent.verification import ComponentTestSpec
from luxar.ports.verification import VerificationToolError


def test_component_test_adapter_uses_fixed_command_and_sanitized_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "tests" / "test_driver.py"
    target.parent.mkdir()
    target.write_text("def test_driver(): pass\n", encoding="utf-8")
    monkeypatch.setenv("LUXAR_SECRET_TOKEN", "do-not-forward")
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="3 passed, 1 skipped in 0.10s\n",
            stderr="",
        )

    monkeypatch.setattr("luxar.adapters.agent_verification.subprocess.run", fake_run)
    adapter = LocalComponentTestAdapter(pytest_command=["python", "-m", "pytest", "-q"])
    evidence = adapter.run_component_test(
        tmp_path,
        ComponentTestSpec(
            test_id="driver-host",
            component_id="driver",
            runner="pytest",
            target="tests/test_driver.py",
            description="驱动 Host 测试",
        ),
    )

    assert evidence.success is True
    assert evidence.passed == 3
    assert evidence.skipped == 1
    assert captured["command"] == [
        "python",
        "-m",
        "pytest",
        "-q",
        "tests/test_driver.py",
    ]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert "LUXAR_SECRET_TOKEN" not in kwargs["env"]


def test_component_test_adapter_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside_test.py"
    outside.write_text("pass\n", encoding="utf-8")
    adapter = LocalComponentTestAdapter()

    with pytest.raises(VerificationToolError) as captured:
        adapter.run_component_test(
            tmp_path,
            ComponentTestSpec(
                test_id="escape",
                component_id="driver",
                runner="pytest",
                target="../outside_test.py",
                description="不能越界",
            ),
        )

    assert captured.value.category == "configuration"


def _write_artifact_fixture(root: Path, *, app_size: int = 700_000) -> None:
    build = root / "build"
    build.mkdir()
    (build / "project_description.json").write_text(
        '{"project_name":"demo","app_bin":"demo.bin"}',
        encoding="utf-8",
    )
    (build / "demo.bin").write_bytes(b"x" * app_size)
    (root / "sdkconfig").write_text(
        'CONFIG_ESPTOOLPY_FLASHSIZE="4MB"\n',
        encoding="utf-8",
    )
    (root / "partitions.csv").write_text(
        "# Name, Type, SubType, Offset, Size\n"
        "nvs,data,nvs,0x9000,0x6000\n"
        "factory,app,factory,0x10000,1M\n",
        encoding="utf-8",
    )


def test_artifact_inspector_reads_image_flash_and_partition_metrics(tmp_path: Path) -> None:
    _write_artifact_fixture(tmp_path)

    evidence = EspIdfArtifactInspectorAdapter().inspect_firmware(tmp_path)

    assert evidence.app_size_bytes == 700_000
    assert evidence.app_partition_size_bytes == 1_048_576
    assert evidence.app_partition_free_bytes == 348_576
    assert evidence.flash_size_bytes == 4 * 1024 * 1024
    assert evidence.partition_table_valid is True
    assert str(tmp_path) not in evidence.summary


def test_artifact_inspector_marks_oversized_application_invalid(tmp_path: Path) -> None:
    _write_artifact_fixture(tmp_path, app_size=1_100_000)

    evidence = EspIdfArtifactInspectorAdapter().inspect_firmware(tmp_path)

    assert evidence.partition_table_valid is False
    assert evidence.app_partition_free_bytes < 0


def test_registered_protocol_adapter_only_uses_known_target(tmp_path: Path) -> None:
    spec = ProtocolProbeSpec(
        probe_id="mqtt-loop",
        protocol="mqtt",
        operation="publish_subscribe",
        target_ref="mqtt.primary",
        description="MQTT 回环",
    )
    adapter = RegisteredProtocolProbeAdapter(
        {
            "mqtt.primary": lambda current: ProtocolProbeEvidence(
                probe_id=current.probe_id,
                protocol=current.protocol,
                operation=current.operation,
                success=True,
                attempts=1,
                successful_exchanges=1,
            )
        }
    )

    assert adapter.run_protocol_probe(tmp_path, spec).success is True
    unknown = spec.model_copy(update={"target_ref": "mqtt.unknown"})
    with pytest.raises(VerificationToolError) as captured:
        adapter.run_protocol_probe(tmp_path, unknown)
    assert captured.value.category == "configuration"


def test_registered_runtime_adapter_only_uses_known_target(tmp_path: Path) -> None:
    spec = RuntimeScenarioSpec(
        scenario_id="wifi-reconnect",
        kind="reconnect",
        target_ref="wifi.primary",
        duration_seconds=10,
        description="Wi-Fi 重连",
    )
    adapter = RegisteredRuntimeScenarioAdapter(
        {
            "wifi.primary": lambda current: RuntimeScenarioEvidence(
                scenario_id=current.scenario_id,
                kind=current.kind,
                success=True,
                observed_duration_seconds=10,
                disconnect_count=1,
                recovery_count=1,
            )
        }
    )

    assert adapter.run_runtime_scenario(tmp_path, spec).success is True
