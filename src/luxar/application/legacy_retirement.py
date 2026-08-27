"""Evidence-backed gates for eventually removing the legacy firmware route."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LegacyRetirementGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    gate_id: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)


LEGACY_RETIREMENT_GATES: tuple[LegacyRetirementGate, ...] = (
    LegacyRetirementGate(
        gate_id="rollback_support_window_elapsed",
        description="Supervisor 默认切换后的一个版本回退承诺已经履行",
    ),
    LegacyRetirementGate(
        gate_id="no_firmware_rollback_usage",
        description="约定观察窗口内没有 legacy 固件回退运行",
    ),
    LegacyRetirementGate(
        gate_id="specialized_workflows_extracted",
        description="项目检查和知识任务不再依赖 legacy 固件图模块",
    ),
    LegacyRetirementGate(
        gate_id="no_legacy_recovery_dependencies",
        description="没有待恢复的 legacy checkpoint 或审批记录",
    ),
    LegacyRetirementGate(
        gate_id="supervisor_regression_passed",
        description="删除候选版本的 Supervisor 完整回归通过",
    ),
)


class LegacyRetirementObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    gate_id: str = Field(min_length=1, max_length=120)
    passed: bool = False
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def passed_gate_requires_evidence(self) -> "LegacyRetirementObservation":
        if self.passed and not self.evidence_ids:
            raise ValueError("通过的 legacy 删除门槛必须提供 evidence_ids")
        return self


class EvaluatedLegacyRetirementGate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    gate_id: str
    description: str
    passed: bool
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""


class LegacyRetirementReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    gates: list[EvaluatedLegacyRetirementGate]
    ready_for_removal: bool
    blocking_gate_ids: list[str]
    unknown_gate_ids: list[str] = Field(default_factory=list)


def evaluate_legacy_retirement(
    observations: Sequence[LegacyRetirementObservation],
) -> LegacyRetirementReport:
    known = {gate.gate_id: gate for gate in LEGACY_RETIREMENT_GATES}
    by_id: dict[str, LegacyRetirementObservation] = {}
    for observation in observations:
        if observation.gate_id in by_id:
            raise ValueError(f"legacy 删除门槛重复: {observation.gate_id}")
        by_id[observation.gate_id] = observation

    evaluated: list[EvaluatedLegacyRetirementGate] = []
    blocking: list[str] = []
    for gate in LEGACY_RETIREMENT_GATES:
        observation = by_id.get(gate.gate_id)
        passed = bool(observation is not None and observation.passed)
        if not passed:
            blocking.append(gate.gate_id)
        evaluated.append(
            EvaluatedLegacyRetirementGate(
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
    return LegacyRetirementReport(
        gates=evaluated,
        ready_for_removal=not blocking and not unknown,
        blocking_gate_ids=blocking,
        unknown_gate_ids=unknown,
    )


def current_legacy_retirement() -> LegacyRetirementReport:
    """Return the honest post-switch baseline; legacy is not removable yet."""

    return evaluate_legacy_retirement(
        [
            LegacyRetirementObservation(
                gate_id="rollback_support_window_elapsed",
                passed=False,
                note="0.1.x 仍是承诺支持的回退窗口",
            ),
            LegacyRetirementObservation(
                gate_id="no_firmware_rollback_usage",
                passed=False,
                note=(
                    "持久化观察基线和 /api/runtime/audit 已建立；"
                    "尚未达到最短观察窗口"
                ),
            ),
            LegacyRetirementObservation(
                gate_id="specialized_workflows_extracted",
                passed=True,
                evidence_ids=[
                    "test:specialized-workflow:isolated-graph-and-bootstrap",
                    "test:web:specialized-dispatch-and-recovery",
                ],
                note=(
                    "新任务使用独立 State、Graph、Runner、Bootstrap 和结果合同；"
                    "legacy 图中的旧节点仅保留用于迁移前 checkpoint 兼容"
                ),
            ),
            LegacyRetirementObservation(
                gate_id="no_legacy_recovery_dependencies",
                passed=False,
                note=(
                    "运行、待审批和 SQLite checkpoint 已可只读审计；"
                    "删除候选版本仍需取得清零证据"
                ),
            ),
            LegacyRetirementObservation(
                gate_id="supervisor_regression_passed",
                passed=True,
                evidence_ids=["pytest:775-passed-14-skipped:2026-08-24"],
            ),
        ]
    )


__all__ = [
    "EvaluatedLegacyRetirementGate",
    "LEGACY_RETIREMENT_GATES",
    "LegacyRetirementGate",
    "LegacyRetirementObservation",
    "LegacyRetirementReport",
    "current_legacy_retirement",
    "evaluate_legacy_retirement",
]
