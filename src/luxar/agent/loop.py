from __future__ import annotations

from luxar.agent.planner import build_runtime_plan


def describe_runtime_loop(task: str) -> dict[str, object]:
    return build_runtime_plan(task)
