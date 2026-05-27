from __future__ import annotations

from typing import Any

from luxar.agent.diagnostics import (
    Evidence,
    EscalationDecision,
    check_escalation,
    collect_evidence,
)
from luxar.agent.planner import build_runtime_plan
from luxar.core.config_manager import ConfigManager


def describe_runtime_loop(task: str) -> dict[str, object]:
    return build_runtime_plan(task)


def execute_runtime_loop(
    task: str,
    project: str = "",
    manager: ConfigManager | None = None,
    *,
    is_hardware_task: bool = False,
    max_attempts: int = 3,
    build_callback: Any = None,
    flash_callback: Any = None,
    monitor_callback: Any = None,
    probe_callback: Any = None,
) -> dict[str, Any]:
    cfg_manager = manager or ConfigManager()
    plan = build_runtime_plan(task)
    attempts = 0
    evidence = collect_evidence()
    escalation = None
    all_lessons = []
    final_success = False

    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        build_ok = False
        flash_ok = False
        monitor_lines = []
        probe_config = {}

        if build_callback is not None:
            try:
                build_result = build_callback(project)
                build_ok = build_result.get("success", False) if isinstance(build_result, dict) else False
            except Exception:
                build_ok = False

        if flash_callback is not None and build_ok:
            try:
                flash_result = flash_callback(project)
                flash_ok = flash_result.get("success", False) if isinstance(flash_result, dict) else False
            except Exception:
                flash_ok = False

        if monitor_callback is not None:
            try:
                monitor_result = monitor_callback(project)
                monitor_lines = monitor_result.get("lines", []) if isinstance(monitor_result, dict) else []
            except Exception:
                monitor_lines = []

        if probe_callback is not None:
            try:
                probe_result = probe_callback(project)
                probe_config = probe_result if isinstance(probe_result, dict) else {}
            except Exception:
                probe_config = {}

        evidence = collect_evidence(
            build_success=build_ok,
            flash_success=flash_ok,
            monitor_lines=monitor_lines,
            probe_config=probe_config,
        )

        if is_hardware_task:
            final_success = evidence.is_sufficient_for_hardware_task()
        else:
            final_success = build_ok

        if final_success:
            break

        escalation = check_escalation(
            evidence=evidence,
            consecutive_failures=attempt,
            max_failures=max_attempts,
            is_hardware_task=is_hardware_task,
        )
        if escalation.should_escalate and attempt >= max_attempts:
            break

    return {
        "success": final_success,
        "task": task,
        "project": project,
        "plan": plan,
        "attempts": attempts,
        "evidence": evidence.summary(),
        "escalation": (
            {
                "should_escalate": escalation.should_escalate,
                "reason": escalation.reason,
                "options": escalation.options,
                "recommendation": escalation.recommendation,
            }
            if escalation
            else None
        ),
        "lessons_recorded": all_lessons,
        "mode": "runtime_loop",
    }
