"""Stage 10 release gate for making the Supervisor runtime the default.

The gate mirrors the implementation plan's Definition of Done.  A passing
claim without evidence is invalid, and a real-hardware smoke test is tracked
separately from the offline regression so software-only CI cannot silently
promote the new runtime.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


class QualificationGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    gate_id: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)


SUPERVISOR_DEFAULT_GATES: tuple[QualificationGate, ...] = (
    QualificationGate(
        gate_id="reference_project",
        description="从空目录自主实现完整参考工程",
    ),
    QualificationGate(
        gate_id="incremental_project_preservation",
        description="接管已有工程并保存未修改能力",
    ),
    QualificationGate(
        gate_id="hierarchical_task_graph",
        description="完整项目使用多层依赖任务图",
    ),
    QualificationGate(
        gate_id="embedded_subsystems",
        description="覆盖多外设、协议、FreeRTOS、存储、网络和 OTA",
    ),
    QualificationGate(
        gate_id="schema_self_repair",
        description="模型格式错误可在预算内自动修复",
    ),
    QualificationGate(
        gate_id="build_self_repair",
        description="构建错误可诊断、修改并重新构建",
    ),
    QualificationGate(
        gate_id="hardware_rule_blocking",
        description="硬件资源规则可在写代码前阻止无效设计",
    ),
    QualificationGate(
        gate_id="interaction_intent_separation",
        description="用户提问与目标或计划修改严格分离",
    ),
    QualificationGate(
        gate_id="cross_process_recovery",
        description="目标、任务、反馈和审批可以跨进程恢复",
    ),
    QualificationGate(
        gate_id="hardware_verification_required",
        description="构建成功不能替代硬件功能验证",
    ),
    QualificationGate(
        gate_id="completion_evidence",
        description="每项完成声明都有工具证据",
    ),
    QualificationGate(
        gate_id="deterministic_approval",
        description="高风险操作仍受确定性审批策略控制",
    ),
    QualificationGate(
        gate_id="runtime_comparison",
        description="同一工程和目标下 Supervisor 不劣于 legacy",
    ),
    QualificationGate(
        gate_id="full_regression",
        description="完整离线回归和集成测试通过",
    ),
    QualificationGate(
        gate_id="real_hardware_smoke",
        description="至少一个真实硬件 smoke test 通过",
    ),
)


class QualificationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    gate_id: str = Field(min_length=1, max_length=120)
    passed: bool = False
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def passed_gate_requires_evidence(self) -> "QualificationObservation":
        if self.passed and not self.evidence_ids:
            raise ValueError("通过的运行时资格门槛必须提供 evidence_ids")
        return self


class EvaluatedQualificationGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    gate_id: str
    description: str
    passed: bool
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""


class SupervisorQualificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    gates: list[EvaluatedQualificationGate]
    ready_for_default: bool
    missing_gate_ids: list[str]
    unknown_gate_ids: list[str] = Field(default_factory=list)


def evaluate_supervisor_qualification(
    observations: Sequence[QualificationObservation],
) -> SupervisorQualificationReport:
    """Evaluate a bounded, evidence-backed Supervisor release report."""

    known = {gate.gate_id: gate for gate in SUPERVISOR_DEFAULT_GATES}
    by_id: dict[str, QualificationObservation] = {}
    for observation in observations:
        if observation.gate_id in by_id:
            raise ValueError(
                f"运行时资格门槛重复: {observation.gate_id}"
            )
        by_id[observation.gate_id] = observation

    evaluated: list[EvaluatedQualificationGate] = []
    missing: list[str] = []
    for gate in SUPERVISOR_DEFAULT_GATES:
        observation = by_id.get(gate.gate_id)
        passed = bool(observation is not None and observation.passed)
        if not passed:
            missing.append(gate.gate_id)
        evaluated.append(
            EvaluatedQualificationGate(
                gate_id=gate.gate_id,
                description=gate.description,
                passed=passed,
                evidence_ids=(
                    list(dict.fromkeys(observation.evidence_ids))
                    if observation is not None
                    else []
                ),
                note=observation.note if observation is not None else "",
            )
        )

    unknown = sorted(set(by_id) - set(known))
    return SupervisorQualificationReport(
        gates=evaluated,
        ready_for_default=not missing and not unknown,
        missing_gate_ids=missing,
        unknown_gate_ids=unknown,
    )


_RELEASE_EVIDENCE_BY_GATE: dict[str, list[str]] = {
    "reference_project": [
        "natural-language-build:2026-08-24:stage10-real-natural-language-reference"
    ],
    "incremental_project_preservation": [
        "test:supervisor-qualification:incremental-preservation"
    ],
    "hierarchical_task_graph": ["test:supervisor-qualification:task-graph"],
    "embedded_subsystems": ["test:full-environment-node:subsystems"],
    "schema_self_repair": ["test:agent-schema-repair"],
    "build_self_repair": ["test:agent-build-recovery"],
    "hardware_rule_blocking": ["test:agent-hardware-rules"],
    "interaction_intent_separation": ["test:agent-intent-separation"],
    "cross_process_recovery": ["test:agent-cross-process-recovery"],
    "hardware_verification_required": ["test:agent-hardware-evidence-gate"],
    "completion_evidence": ["test:agent-acceptance-evidence"],
    "deterministic_approval": ["test:agent-approval-interrupt"],
    "runtime_comparison": ["test:runtime-comparison:full-environment-node"],
    "full_regression": ["pytest:775-passed-14-skipped:2026-08-24"],
    "real_hardware_smoke": [
        "hardware-smoke:af5d49a16478ef963f24d62820ebeccf5ef3e27c2312b31e8329c13e559c95ea"
    ],
}


def current_supervisor_qualification() -> SupervisorQualificationReport:
    """Return the audited Stage 10 release qualification bundled with LUXAR."""

    observations = [
        QualificationObservation(
            gate_id=gate.gate_id,
            passed=gate.gate_id in _RELEASE_EVIDENCE_BY_GATE,
            evidence_ids=_RELEASE_EVIDENCE_BY_GATE.get(gate.gate_id, []),
        )
        for gate in SUPERVISOR_DEFAULT_GATES
    ]
    return evaluate_supervisor_qualification(observations)


__all__ = [
    "EvaluatedQualificationGate",
    "QualificationGate",
    "QualificationObservation",
    "SUPERVISOR_DEFAULT_GATES",
    "SupervisorQualificationReport",
    "current_supervisor_qualification",
    "evaluate_supervisor_qualification",
]
