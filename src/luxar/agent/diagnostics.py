from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Evidence:
    build_passed: bool = False
    flash_passed: bool = False
    monitor_output: list[str] = field(default_factory=list)
    probe_config: dict[str, Any] = field(default_factory=dict)
    hardware_checks: list[dict[str, Any]] = field(default_factory=list)

    def is_sufficient_for_hardware_task(self) -> bool:
        return self.build_passed and (self.flash_passed or bool(self.probe_config))

    def summary(self) -> dict[str, Any]:
        return {
            "build_passed": self.build_passed,
            "flash_passed": self.flash_passed,
            "monitor_lines": len(self.monitor_output),
            "probe_keys": sorted(self.probe_config.keys()) if self.probe_config else [],
            "hardware_checks_passed": sum(1 for c in self.hardware_checks if c.get("passed")),
            "hardware_checks_total": len(self.hardware_checks),
        }


@dataclass(slots=True)
class EscalationDecision:
    should_escalate: bool
    reason: str
    context: dict[str, Any] = field(default_factory=dict)
    options: list[str] = field(default_factory=list)
    recommendation: str = ""
    evidence_summary: dict[str, Any] = field(default_factory=dict)


def summarize_runtime_state(skills: int, executable_skills: int, lessons: int) -> dict[str, int]:
    return {
        "skills": int(skills),
        "executable_skills": int(executable_skills),
        "lessons": int(lessons),
    }


def collect_evidence(
    *,
    build_success: bool = False,
    flash_success: bool = False,
    monitor_lines: list[str] | None = None,
    probe_config: dict[str, Any] | None = None,
    hardware_checks: list[dict[str, Any]] | None = None,
) -> Evidence:
    return Evidence(
        build_passed=build_success,
        flash_passed=flash_success,
        monitor_output=list(monitor_lines or []),
        probe_config=dict(probe_config or {}),
        hardware_checks=list(hardware_checks or []),
    )


def check_escalation(
    *,
    evidence: Evidence,
    consecutive_failures: int = 0,
    max_failures: int = 3,
    is_hardware_task: bool = False,
    requires_irreversible_action: bool = False,
    missing_skill_coverage: bool = False,
) -> EscalationDecision:
    reasons: list[str] = []
    context: dict[str, Any] = {}
    options: list[str] = []

    if requires_irreversible_action:
        reasons.append("Task requires an irreversible action (e.g., flash or fuse write).")
        options.append("Approve irreversible action with explicit confirmation.")
        options.append("Run in a safer simulation/dry-run mode.")

    if consecutive_failures >= max_failures:
        reasons.append(f"{consecutive_failures} consecutive failures reached the limit of {max_failures}.")
        context["consecutive_failures"] = consecutive_failures
        options.append("Review failure logs and adjust the approach.")
        options.append("Check if the task intent is still valid.")

    if is_hardware_task and not evidence.is_sufficient_for_hardware_task():
        reasons.append("Hardware task lacks sufficient runtime evidence (build/flash/probe).")
        context["evidence"] = evidence.summary()
        options.append("Verify toolchain and hardware connections.")
        options.append("Provide explicit hardware configuration.")

    if missing_skill_coverage:
        reasons.append("No matching skills found for this task domain.")
        options.append("Write a new skill or provide explicit instructions.")

    should_escalate = bool(reasons)
    recommendation = "; ".join(options[:2]) if (should_escalate and options) else ""
    if not should_escalate:
        recommendation = "No escalation needed."

    return EscalationDecision(
        should_escalate=should_escalate,
        reason="; ".join(reasons) if reasons else "No escalation needed.",
        context=context,
        options=options,
        recommendation=recommendation,
        evidence_summary=evidence.summary(),
    )
