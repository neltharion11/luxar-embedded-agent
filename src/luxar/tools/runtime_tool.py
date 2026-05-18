from __future__ import annotations

from luxar.agent.runtime import explain_runtime, run_runtime_task
from luxar.core.config_manager import ConfigManager


def run_runtime(task: str, project: str = "") -> dict[str, object]:
    return run_runtime_task(task=task, project=project, manager=ConfigManager())


def explain_runtime_tool() -> dict[str, object]:
    return explain_runtime()
