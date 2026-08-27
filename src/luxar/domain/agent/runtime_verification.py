"""协议探测、回环与设备韧性场景的结构化验证合同。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    check_id: str = Field(min_length=1, max_length=200)
    passed: bool
    evidence_id: str | None = Field(default=None, max_length=260)
    summary: str = Field(min_length=1, max_length=1000)


class ProtocolProbeSpec(BaseModel):
    """引用预配置目标，不接受原始 URL、凭据或命令。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    probe_id: str = Field(min_length=1, max_length=200)
    protocol: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    operation: Literal[
        "connect",
        "request_response",
        "publish_subscribe",
        "loopback",
    ]
    target_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
    payload_ref: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$",
    )
    requires_device: bool = False
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    minimum_successful_exchanges: int = Field(default=1, ge=1, le=10_000)
    maximum_latency_ms: float | None = Field(default=None, gt=0)
    description: str = Field(min_length=1, max_length=1000)


class ProtocolProbeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    probe_id: str = Field(min_length=1, max_length=200)
    protocol: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    operation: Literal[
        "connect",
        "request_response",
        "publish_subscribe",
        "loopback",
    ]
    success: bool
    attempts: int = Field(ge=1, le=10_000)
    successful_exchanges: int = Field(default=0, ge=0, le=10_000)
    maximum_latency_ms: float | None = Field(default=None, ge=0)
    response_summary: str = Field(default="", max_length=8000)
    failure_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_result_consistency(self) -> "ProtocolProbeEvidence":
        if self.success and self.failure_reason is not None:
            raise ValueError("成功的协议探测不能包含失败原因")
        if self.success and self.successful_exchanges == 0:
            raise ValueError("成功的协议探测必须至少完成一次交换")
        if not self.success and self.failure_reason is None:
            raise ValueError("失败的协议探测必须提供失败原因")
        return self


class RuntimeScenarioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scenario_id: str = Field(min_length=1, max_length=200)
    kind: Literal["reconnect", "error_injection", "soak"]
    target_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
    duration_seconds: int = Field(ge=1, le=604_800)
    maximum_error_count: int = Field(default=0, ge=0)
    maximum_recovery_time_ms: float | None = Field(default=None, gt=0)
    minimum_heap_headroom_bytes: int | None = Field(default=None, ge=0)
    description: str = Field(min_length=1, max_length=1000)


class RuntimeScenarioEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scenario_id: str = Field(min_length=1, max_length=200)
    kind: Literal["reconnect", "error_injection", "soak"]
    success: bool
    observed_duration_seconds: float = Field(ge=0)
    error_count: int = Field(default=0, ge=0)
    disconnect_count: int = Field(default=0, ge=0)
    recovery_count: int = Field(default=0, ge=0)
    maximum_recovery_time_ms: float | None = Field(default=None, ge=0)
    injected_fault_count: int = Field(default=0, ge=0)
    recovered_fault_count: int = Field(default=0, ge=0)
    minimum_free_heap_bytes: int | None = Field(default=None, ge=0)
    heap_delta_bytes: int | None = None
    watchdog_reset_count: int = Field(default=0, ge=0)
    summary: str = Field(default="", max_length=8000)
    failure_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_result_consistency(self) -> "RuntimeScenarioEvidence":
        if self.success and self.failure_reason is not None:
            raise ValueError("成功的运行场景不能包含失败原因")
        if self.success and self.watchdog_reset_count > 0:
            raise ValueError("发生看门狗复位的运行场景不能标记成功")
        if not self.success and self.failure_reason is None:
            raise ValueError("失败的运行场景必须提供失败原因")
        return self


class ProtocolProbeVerifier:
    def verify(
        self,
        spec: ProtocolProbeSpec,
        evidence: ProtocolProbeEvidence,
    ) -> EvidenceCheckResult:
        failures: list[str] = []
        if evidence.probe_id != spec.probe_id:
            failures.append("probe_id 不匹配")
        if evidence.protocol != spec.protocol or evidence.operation != spec.operation:
            failures.append("协议或操作不匹配")
        if not evidence.success:
            failures.append(evidence.failure_reason or "探测失败")
        if evidence.successful_exchanges < spec.minimum_successful_exchanges:
            failures.append("成功交换次数不足")
        if spec.maximum_latency_ms is not None:
            if evidence.maximum_latency_ms is None:
                failures.append("缺少延迟测量")
            elif evidence.maximum_latency_ms > spec.maximum_latency_ms:
                failures.append("最大延迟超限")
        passed = not failures
        return EvidenceCheckResult(
            check_id=spec.probe_id,
            passed=passed,
            evidence_id=f"protocol-probe:{spec.probe_id}" if passed else None,
            summary="协议探测通过" if passed else "；".join(failures),
        )


class RuntimeScenarioVerifier:
    def verify(
        self,
        spec: RuntimeScenarioSpec,
        evidence: RuntimeScenarioEvidence,
    ) -> EvidenceCheckResult:
        failures: list[str] = []
        if evidence.scenario_id != spec.scenario_id or evidence.kind != spec.kind:
            failures.append("运行场景身份不匹配")
        if not evidence.success:
            failures.append(evidence.failure_reason or "运行场景失败")
        if evidence.observed_duration_seconds < spec.duration_seconds:
            failures.append("观测时长不足")
        if evidence.error_count > spec.maximum_error_count:
            failures.append("错误数量超限")
        if evidence.watchdog_reset_count > 0:
            failures.append("检测到看门狗复位")
        if spec.kind == "reconnect" and evidence.recovery_count < evidence.disconnect_count:
            failures.append("存在未恢复的断线")
        if (
            spec.kind == "error_injection"
            and evidence.recovered_fault_count < evidence.injected_fault_count
        ):
            failures.append("存在未恢复的注入故障")
        if spec.maximum_recovery_time_ms is not None:
            if evidence.maximum_recovery_time_ms is None:
                failures.append("缺少恢复时间测量")
            elif evidence.maximum_recovery_time_ms > spec.maximum_recovery_time_ms:
                failures.append("恢复时间超限")
        if spec.minimum_heap_headroom_bytes is not None:
            if evidence.minimum_free_heap_bytes is None:
                failures.append("缺少最小空闲堆测量")
            elif evidence.minimum_free_heap_bytes < spec.minimum_heap_headroom_bytes:
                failures.append("最小空闲堆低于阈值")
        passed = not failures
        return EvidenceCheckResult(
            check_id=spec.scenario_id,
            passed=passed,
            evidence_id=f"runtime-scenario:{spec.scenario_id}" if passed else None,
            summary="运行场景通过" if passed else "；".join(failures),
        )


__all__ = [
    "EvidenceCheckResult",
    "ProtocolProbeEvidence",
    "ProtocolProbeSpec",
    "ProtocolProbeVerifier",
    "RuntimeScenarioEvidence",
    "RuntimeScenarioSpec",
    "RuntimeScenarioVerifier",
]
