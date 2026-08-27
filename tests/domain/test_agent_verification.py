from __future__ import annotations

import pytest
from pydantic import ValidationError

from luxar.domain.agent.verification import (
    ComponentTestEvidence,
    DeviceLogAssertion,
    DeviceLogVerifier,
    FirmwareMetricAssertion,
    FirmwareResourceEvidence,
    FirmwareResourceVerifier,
    SourceAssertion,
    SourceAssertionVerifier,
    VerificationPlan,
)
from luxar.domain.devices import DeviceLogDiagnostic, MonitorEvidence
from luxar.domain.repairs import ProjectFile


def test_source_assertions_support_path_text_and_regex_checks() -> None:
    files = [
        ProjectFile(
            path="main/main.c",
            content="void app_main(void) { gpio_set_level(GPIO_NUM_33, 1); }\n",
        )
    ]
    assertions = [
        SourceAssertion(
            assertion_id="gpio33-present",
            operator="contains",
            pattern="GPIO_NUM_33",
            path="main/main.c",
            description="GPIO33 已接入",
        ),
        SourceAssertion(
            assertion_id="gpio34-absent",
            operator="not_contains",
            pattern="GPIO_NUM_34",
            description="未使用 GPIO34",
        ),
        SourceAssertion(
            assertion_id="level-call",
            operator="regex",
            pattern=r"gpio_set_level\(GPIO_NUM_33,\s*1\)",
            description="输出高电平",
        ),
    ]

    results = SourceAssertionVerifier().verify(assertions, files)

    assert all(result.passed for result in results)
    assert [result.evidence_id for result in results] == [
        "source-assert:gpio33-present",
        "source-assert:gpio34-absent",
        "source-assert:level-call",
    ]


def test_source_assertion_fails_when_target_file_is_missing() -> None:
    assertion = SourceAssertion(
        assertion_id="missing-file",
        operator="contains",
        pattern="app_main",
        path="main/missing.c",
        description="目标文件存在",
    )

    result = SourceAssertionVerifier().verify([assertion], [])[0]

    assert result.passed is False
    assert result.evidence_id is None
    assert "不存在" in result.summary


def test_device_log_assertion_rejects_fatal_diagnostic() -> None:
    evidence = MonitorEvidence(
        command=["idf.py", "monitor"],
        port="COM3",
        capture_timeout_seconds=10,
        captured_log="boot ok\nTask watchdog got triggered",
        terminated_by_timeout=True,
        diagnostics=[
            DeviceLogDiagnostic(
                kind="watchdog",
                summary="任务看门狗触发",
            )
        ],
    )
    assertion = DeviceLogAssertion(
        assertion_id="healthy-runtime",
        operator="no_fatal_diagnostics",
        description="运行期无致命诊断",
    )

    result = DeviceLogVerifier().verify([assertion], evidence)[0]

    assert result.passed is False
    assert result.evidence_id is None


def test_device_assertions_require_device_verification() -> None:
    with pytest.raises(ValidationError):
        VerificationPlan(
            require_device=False,
            device_assertions=[
                DeviceLogAssertion(
                    assertion_id="boot-ok",
                    operator="contains",
                    pattern="boot ok",
                    description="设备启动成功",
                )
            ],
        )


def test_component_test_evidence_rejects_false_success() -> None:
    with pytest.raises(ValidationError):
        ComponentTestEvidence(
            test_id="driver-host",
            success=True,
            runner="pytest",
            command=["pytest", "tests/driver"],
            return_code=0,
            passed=3,
            failed=1,
        )


def test_firmware_resource_verifier_checks_size_partition_and_stack() -> None:
    evidence = FirmwareResourceEvidence(
        command=["idf.py", "size"],
        app_size_bytes=700_000,
        app_partition_size_bytes=1_000_000,
        flash_size_bytes=4_000_000,
        dram_static_bytes=80_000,
        iram_static_bytes=50_000,
        minimum_task_stack_headroom_bytes=1_024,
        partition_table_valid=True,
    )
    assertions = [
        FirmwareMetricAssertion(
            assertion_id="app-fits",
            metric="app_partition_free_bytes",
            operator="gte",
            expected=200_000,
            description="应用分区保留足够空间",
        ),
        FirmwareMetricAssertion(
            assertion_id="stack-safe",
            metric="minimum_task_stack_headroom_bytes",
            operator="gte",
            expected=2_048,
            description="任务栈余量满足要求",
        ),
        FirmwareMetricAssertion(
            assertion_id="partition-valid",
            metric="partition_table_valid",
            operator="eq",
            expected=True,
            description="分区表有效",
        ),
    ]

    results = FirmwareResourceVerifier().verify(assertions, evidence)

    assert [result.passed for result in results] == [True, False, True]
    assert results[0].evidence_id == "firmware-assert:app-fits"
    assert results[1].evidence_id is None


def test_firmware_assertions_require_build() -> None:
    with pytest.raises(ValidationError):
        VerificationPlan(
            require_build=False,
            firmware_assertions=[
                FirmwareMetricAssertion(
                    assertion_id="app-fits",
                    metric="app_size_bytes",
                    operator="lte",
                    expected=1_000_000,
                    description="应用大小不超限",
                )
            ],
        )
