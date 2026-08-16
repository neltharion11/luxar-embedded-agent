import json

import pytest
from pydantic import ValidationError

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.log_analyst import DeepSeekLogAnalyst
from luxar.domain.devices import (
    DeviceDiagnosis,
    DeviceLogDiagnostic,
    MonitorEvidence,
)
from luxar.domain.requirements import FirmwareRequirement
from luxar.ports.errors import CapabilityError


def make_requirement() -> FirmwareRequirement:
    return FirmwareRequirement(
        target="esp32",
        feature="gpio_blink",
        gpio=2,
    )


def make_evidence() -> MonitorEvidence:
    return MonitorEvidence(
        command=["idf.py", "-p", "COM3", "monitor"],
        port="COM3",
        capture_timeout_seconds=10,
        captured_log="Guru Meditation Error: Core 0 panic'ed",
        terminated_by_timeout=True,
        diagnostics=[
            DeviceLogDiagnostic(
                kind="panic",
                summary="Guru Meditation 崩溃",
                lines=["Guru Meditation Error"],
            )
        ],
    )


def test_analyst_converts_schema_response_to_diagnosis() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "healthy": False,
                "repair_needed": True,
                "summary": "设备崩溃",
                "findings": ["Guru Meditation"],
            }
        ]
    )
    analyst = DeepSeekLogAnalyst(client, "deepseek-v4-pro")

    diagnosis = analyst.analyze(make_requirement(), make_evidence())

    assert diagnosis == DeviceDiagnosis(
        healthy=False,
        repair_needed=True,
        summary="设备崩溃",
        findings=["Guru Meditation"],
    )


def test_analyst_sends_evidence_schema_and_repair_model() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "healthy": True,
                "repair_needed": False,
                "summary": "运行正常",
                "findings": [],
            }
        ]
    )
    analyst = DeepSeekLogAnalyst(client, "deepseek-v4-pro")
    requirement = make_requirement()
    evidence = make_evidence()

    analyst.analyze(requirement, evidence)

    system_prompt, user_prompt, model = client.calls[0]
    payload = json.loads(user_prompt)
    assert "JSON Schema" in system_prompt
    assert '"healthy"' in system_prompt
    assert "不可信数据" in system_prompt
    assert "禁止声称构建或烧录已经成功" in system_prompt
    assert payload == {
        "requirement": requirement.model_dump(mode="json"),
        "monitor_evidence": evidence.model_dump(mode="json"),
    }
    assert model == "deepseek-v4-pro"


@pytest.mark.parametrize(
    "payload",
    [
        {"healthy": ["yes"], "repair_needed": False, "summary": "bad type"},
        {"healthy": True, "repair_needed": True, "summary": "矛盾"},
        {"healthy": False, "repair_needed": False, "summary": ""},
        {"healthy": True, "repair_needed": False},
    ],
)
def test_analyst_rejects_invalid_model_output(
    payload: dict[str, object],
) -> None:
    client = FakeJsonCompletionClient([payload])
    analyst = DeepSeekLogAnalyst(client, "deepseek-v4-pro")

    with pytest.raises(CapabilityError) as captured:
        analyst.analyze(make_requirement(), make_evidence())

    assert captured.value.category == "invalid_schema"
    assert captured.value.retryable is False
    assert isinstance(captured.value.__cause__, ValidationError)
