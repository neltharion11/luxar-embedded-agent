from __future__ import annotations

import pytest
from pydantic import ValidationError

from luxar.domain.agent.runtime_verification import (
    ProtocolProbeEvidence,
    ProtocolProbeSpec,
    ProtocolProbeVerifier,
    RuntimeScenarioEvidence,
    RuntimeScenarioSpec,
    RuntimeScenarioVerifier,
)
from luxar.domain.agent.verification import VerificationPlan


def test_protocol_probe_target_must_be_a_preconfigured_reference() -> None:
    with pytest.raises(ValidationError):
        ProtocolProbeSpec(
            probe_id="mqtt-loop",
            protocol="mqtt",
            operation="publish_subscribe",
            target_ref="mqtt://user:secret@example.com/topic",
            description="MQTT 回环",
        )


def test_protocol_probe_verifier_checks_exchange_count_and_latency() -> None:
    spec = ProtocolProbeSpec(
        probe_id="mqtt-loop",
        protocol="mqtt",
        operation="publish_subscribe",
        target_ref="mqtt.primary",
        minimum_successful_exchanges=3,
        maximum_latency_ms=100,
        description="MQTT 发布订阅回环",
    )
    evidence = ProtocolProbeEvidence(
        probe_id="mqtt-loop",
        protocol="mqtt",
        operation="publish_subscribe",
        success=True,
        attempts=3,
        successful_exchanges=3,
        maximum_latency_ms=120,
    )

    result = ProtocolProbeVerifier().verify(spec, evidence)

    assert result.passed is False
    assert result.evidence_id is None
    assert "延迟超限" in result.summary


def test_runtime_reconnect_requires_every_disconnect_to_recover() -> None:
    spec = RuntimeScenarioSpec(
        scenario_id="wifi-reconnect",
        kind="reconnect",
        target_ref="wifi.primary",
        duration_seconds=30,
        maximum_recovery_time_ms=2_000,
        description="Wi-Fi 断线重连",
    )
    evidence = RuntimeScenarioEvidence(
        scenario_id="wifi-reconnect",
        kind="reconnect",
        success=True,
        observed_duration_seconds=30,
        disconnect_count=2,
        recovery_count=1,
        maximum_recovery_time_ms=500,
    )

    result = RuntimeScenarioVerifier().verify(spec, evidence)

    assert result.passed is False
    assert "未恢复" in result.summary


def test_soak_scenario_checks_duration_errors_and_heap_headroom() -> None:
    spec = RuntimeScenarioSpec(
        scenario_id="mqtt-soak",
        kind="soak",
        target_ref="mqtt.primary",
        duration_seconds=3_600,
        maximum_error_count=1,
        minimum_heap_headroom_bytes=64_000,
        description="MQTT 长期运行",
    )
    evidence = RuntimeScenarioEvidence(
        scenario_id="mqtt-soak",
        kind="soak",
        success=True,
        observed_duration_seconds=3_600,
        error_count=1,
        minimum_free_heap_bytes=80_000,
        heap_delta_bytes=-128,
    )

    result = RuntimeScenarioVerifier().verify(spec, evidence)

    assert result.passed is True
    assert result.evidence_id == "runtime-scenario:mqtt-soak"


def test_device_probe_and_runtime_scenario_require_device_verification() -> None:
    probe = ProtocolProbeSpec(
        probe_id="mqtt-device",
        protocol="mqtt",
        operation="connect",
        target_ref="mqtt.primary",
        requires_device=True,
        description="设备连接 MQTT",
    )
    scenario = RuntimeScenarioSpec(
        scenario_id="wifi-reconnect",
        kind="reconnect",
        target_ref="wifi.primary",
        duration_seconds=30,
        description="Wi-Fi 重连",
    )

    with pytest.raises(ValidationError):
        VerificationPlan(require_build=False, protocol_probes=[probe])
    with pytest.raises(ValidationError):
        VerificationPlan(require_build=False, runtime_scenarios=[scenario])
