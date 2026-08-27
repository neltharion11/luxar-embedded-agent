"""源码、构建与设备运行证据的确定性验证合同。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from luxar.domain.devices import FlashEvidence, MonitorEvidence
from luxar.domain.evidence import BuildEvidence
from luxar.domain.repairs import ProjectFile
from luxar.domain.agent.runtime_verification import (
    EvidenceCheckResult,
    ProtocolProbeEvidence,
    ProtocolProbeSpec,
    RuntimeScenarioEvidence,
    RuntimeScenarioSpec,
)


class SourceAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    assertion_id: str = Field(min_length=1, max_length=200)
    operator: Literal["contains", "not_contains", "regex"]
    pattern: str = Field(min_length=1, max_length=2000)
    path: str | None = Field(default=None, max_length=400)
    description: str = Field(min_length=1, max_length=1000)


class DeviceLogAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    assertion_id: str = Field(min_length=1, max_length=200)
    operator: Literal["contains", "not_contains", "regex", "no_fatal_diagnostics"]
    pattern: str | None = Field(default=None, max_length=2000)
    description: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_pattern(self) -> "DeviceLogAssertion":
        if self.operator != "no_fatal_diagnostics" and not self.pattern:
            raise ValueError("设备日志文本断言必须提供 pattern")
        return self


class AssertionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    assertion_id: str = Field(min_length=1, max_length=200)
    passed: bool
    evidence_id: str | None = Field(default=None, max_length=260)
    summary: str = Field(min_length=1, max_length=1000)


class ComponentTestSpec(BaseModel):
    """由受控测试执行器解释的组件测试规格，不携带任意 shell 命令。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    test_id: str = Field(min_length=1, max_length=200)
    component_id: str = Field(min_length=1, max_length=200)
    runner: Literal["pytest", "ctest", "idf_unity"]
    target: str = Field(min_length=1, max_length=400)
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    description: str = Field(min_length=1, max_length=1000)


class ComponentTestEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    test_id: str = Field(min_length=1, max_length=200)
    success: bool
    runner: Literal["pytest", "ctest", "idf_unity"]
    command: list[str] = Field(min_length=1, max_length=40)
    return_code: int
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    output_summary: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def validate_result_consistency(self) -> "ComponentTestEvidence":
        if self.success and (self.return_code != 0 or self.failed != 0):
            raise ValueError("成功的组件测试不能包含失败用例或非零返回码")
        if not self.success and self.return_code == 0:
            raise ValueError("失败的组件测试不能使用零返回码")
        return self


class FirmwareMetricAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    assertion_id: str = Field(min_length=1, max_length=200)
    metric: Literal[
        "app_size_bytes",
        "app_partition_free_bytes",
        "flash_usage_percent",
        "dram_static_bytes",
        "iram_static_bytes",
        "minimum_task_stack_headroom_bytes",
        "partition_table_valid",
    ]
    operator: Literal["lte", "gte", "eq"]
    expected: int | float | bool
    description: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_expected_type(self) -> "FirmwareMetricAssertion":
        if self.metric == "partition_table_valid":
            if not isinstance(self.expected, bool) or self.operator != "eq":
                raise ValueError("分区表有效性断言必须使用布尔值和 eq")
        elif isinstance(self.expected, bool):
            raise ValueError("数值型固件指标不能使用布尔期望值")
        return self


class FirmwareResourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    command: list[str] = Field(min_length=1, max_length=40)
    app_size_bytes: int = Field(ge=0)
    app_partition_size_bytes: int = Field(gt=0)
    flash_size_bytes: int = Field(gt=0)
    dram_static_bytes: int | None = Field(default=None, ge=0)
    iram_static_bytes: int | None = Field(default=None, ge=0)
    minimum_task_stack_headroom_bytes: int | None = Field(default=None, ge=0)
    partition_table_valid: bool
    summary: str = Field(default="", max_length=8000)

    @property
    def app_partition_free_bytes(self) -> int:
        return self.app_partition_size_bytes - self.app_size_bytes

    @property
    def flash_usage_percent(self) -> float:
        return self.app_size_bytes * 100.0 / self.flash_size_bytes


class VerificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_assertions: list[SourceAssertion] = Field(default_factory=list, max_length=100)
    component_tests: list[ComponentTestSpec] = Field(default_factory=list, max_length=100)
    require_build: bool = True
    require_flash: bool = False
    firmware_assertions: list[FirmwareMetricAssertion] = Field(
        default_factory=list,
        max_length=100,
    )
    require_device: bool = False
    device_assertions: list[DeviceLogAssertion] = Field(default_factory=list, max_length=100)
    protocol_probes: list[ProtocolProbeSpec] = Field(default_factory=list, max_length=100)
    runtime_scenarios: list[RuntimeScenarioSpec] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_device_requirements(self) -> "VerificationPlan":
        if self.device_assertions and not self.require_device:
            raise ValueError("存在设备日志断言时必须 require_device=True")
        if self.require_flash and not self.require_build:
            raise ValueError("烧录依赖成功构建，必须 require_build=True")
        if self.firmware_assertions and not self.require_build:
            raise ValueError("固件资源断言依赖成功构建，必须 require_build=True")
        if any(probe.requires_device for probe in self.protocol_probes) and not self.require_device:
            raise ValueError("设备侧协议探测必须 require_device=True")
        if self.runtime_scenarios and not self.require_device:
            raise ValueError("运行韧性场景必须 require_device=True")
        return self


class VerificationRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: str = Field(min_length=1, max_length=240)
    source_results: list[AssertionResult] = Field(default_factory=list, max_length=100)
    component_test_evidence: list[ComponentTestEvidence] = Field(
        default_factory=list,
        max_length=100,
    )
    build_evidence: BuildEvidence | None = None
    flash_evidence: FlashEvidence | None = None
    firmware_resource_evidence: FirmwareResourceEvidence | None = None
    firmware_results: list[AssertionResult] = Field(default_factory=list, max_length=100)
    monitor_evidence: MonitorEvidence | None = None
    device_results: list[AssertionResult] = Field(default_factory=list, max_length=100)
    protocol_probe_evidence: list[ProtocolProbeEvidence] = Field(
        default_factory=list,
        max_length=100,
    )
    protocol_results: list[EvidenceCheckResult] = Field(default_factory=list, max_length=100)
    runtime_scenario_evidence: list[RuntimeScenarioEvidence] = Field(
        default_factory=list,
        max_length=100,
    )
    runtime_results: list[EvidenceCheckResult] = Field(default_factory=list, max_length=100)
    build_verified: bool = False
    hardware_verified: bool = False
    success: bool = False


class SourceAssertionVerifier:
    def verify(
        self,
        assertions: Sequence[SourceAssertion],
        files: Sequence[ProjectFile],
    ) -> list[AssertionResult]:
        by_path = {item.path: item.content for item in files}
        all_content = "\n".join(item.content for item in files)
        results: list[AssertionResult] = []
        for assertion in assertions:
            if assertion.path is None:
                content = all_content
                missing = False
            else:
                content = by_path.get(assertion.path, "")
                missing = assertion.path not in by_path
            if missing:
                passed = False
                summary = f"断言目标文件不存在：{assertion.path}"
            elif assertion.operator == "contains":
                passed = assertion.pattern in content
                summary = "源码包含目标文本" if passed else "源码缺少目标文本"
            elif assertion.operator == "not_contains":
                passed = assertion.pattern not in content
                summary = "源码未包含禁止文本" if passed else "源码包含禁止文本"
            else:
                try:
                    passed = re.search(assertion.pattern, content, re.MULTILINE) is not None
                    summary = "源码正则断言通过" if passed else "源码正则断言未匹配"
                except re.error:
                    passed = False
                    summary = "源码正则表达式无效"
            results.append(
                AssertionResult(
                    assertion_id=assertion.assertion_id,
                    passed=passed,
                    evidence_id=(
                        f"source-assert:{assertion.assertion_id}" if passed else None
                    ),
                    summary=summary,
                )
            )
        return results


class DeviceLogVerifier:
    _FATAL_KINDS = frozenset({"panic", "abort", "assert", "watchdog", "boot_loop", "error"})

    def verify(
        self,
        assertions: Sequence[DeviceLogAssertion],
        evidence: MonitorEvidence,
    ) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        for assertion in assertions:
            if assertion.operator == "no_fatal_diagnostics":
                fatal = [
                    diagnostic
                    for diagnostic in evidence.diagnostics
                    if diagnostic.kind in self._FATAL_KINDS
                ]
                passed = not fatal
                summary = "设备日志无致命诊断" if passed else "设备日志包含致命诊断"
            elif assertion.operator == "contains":
                assert assertion.pattern is not None
                passed = assertion.pattern in evidence.captured_log
                summary = "设备日志包含目标文本" if passed else "设备日志缺少目标文本"
            elif assertion.operator == "not_contains":
                assert assertion.pattern is not None
                passed = assertion.pattern not in evidence.captured_log
                summary = "设备日志未包含禁止文本" if passed else "设备日志包含禁止文本"
            else:
                assert assertion.pattern is not None
                try:
                    passed = re.search(
                        assertion.pattern,
                        evidence.captured_log,
                        re.MULTILINE,
                    ) is not None
                    summary = "设备日志正则断言通过" if passed else "设备日志正则断言未匹配"
                except re.error:
                    passed = False
                    summary = "设备日志正则表达式无效"
            results.append(
                AssertionResult(
                    assertion_id=assertion.assertion_id,
                    passed=passed,
                    evidence_id=(f"device-assert:{assertion.assertion_id}" if passed else None),
                    summary=summary,
                )
            )
        return results


class FirmwareResourceVerifier:
    def verify(
        self,
        assertions: Sequence[FirmwareMetricAssertion],
        evidence: FirmwareResourceEvidence,
    ) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        for assertion in assertions:
            actual = getattr(evidence, assertion.metric)
            if actual is None:
                passed = False
                summary = f"固件指标不可用：{assertion.metric}"
            elif assertion.operator == "lte":
                passed = actual <= assertion.expected
                summary = f"{assertion.metric}={actual}，要求 <= {assertion.expected}"
            elif assertion.operator == "gte":
                passed = actual >= assertion.expected
                summary = f"{assertion.metric}={actual}，要求 >= {assertion.expected}"
            else:
                passed = actual == assertion.expected
                summary = f"{assertion.metric}={actual}，要求 == {assertion.expected}"
            results.append(
                AssertionResult(
                    assertion_id=assertion.assertion_id,
                    passed=passed,
                    evidence_id=(
                        f"firmware-assert:{assertion.assertion_id}" if passed else None
                    ),
                    summary=summary,
                )
            )
        return results


__all__ = [
    "AssertionResult",
    "ComponentTestEvidence",
    "ComponentTestSpec",
    "DeviceLogAssertion",
    "DeviceLogVerifier",
    "FirmwareMetricAssertion",
    "FirmwareResourceEvidence",
    "FirmwareResourceVerifier",
    "SourceAssertion",
    "SourceAssertionVerifier",
    "VerificationPlan",
    "VerificationRun",
]
