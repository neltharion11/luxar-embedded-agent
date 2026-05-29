from __future__ import annotations

from typing import Any

from luxar.agent.loop import execute_runtime_loop
from luxar.agent.runtime import explain_runtime, run_runtime_task
from luxar.core.config_manager import ConfigManager
from luxar.tools.build_project import run_build_project
from luxar.tools.flash_project import run_flash_project
from luxar.tools.monitor_project import run_monitor_project
from luxar.tools.probe_project import run_probe_project


def run_runtime(task: str, project: str = "") -> dict[str, object]:
    return run_runtime_task(task=task, project=project, manager=ConfigManager())


def run_runtime_loop(
    task: str,
    project: str = "",
    *,
    is_hardware_task: bool = False,
    max_attempts: int = 3,
    clean: bool = False,
    probe_type: str = "i2c",
    port: str = "",
    baudrate: int = 115200,
) -> dict[str, Any]:
    cfg_manager = ConfigManager()
    cfg = cfg_manager.ensure_default_config()
    workspace_root = str(cfg_manager.workspace_root())

    def _build(project_path: str) -> dict[str, Any]:
        result = run_build_project(
            project_path=str(cfg_manager.workspace_root() / (project_path or project)),
            config=cfg,
            project_root=str(cfg_manager.project_root()),
            clean=clean,
        )
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return result if isinstance(result, dict) else {"success": bool(result)}

    def _flash(project_path: str) -> dict[str, Any]:
        result = run_flash_project(
            project_path=str(cfg_manager.workspace_root() / (project_path or project)),
            config=cfg,
            project_root=str(cfg_manager.project_root()),
            probe=None,
        )
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return result if isinstance(result, dict) else {"success": bool(result)}

    def _monitor(project_path: str) -> dict[str, Any]:
        result = run_monitor_project(
            project_path=str(cfg_manager.workspace_root() / (project_path or project)),
            config=cfg,
            project_root=str(cfg_manager.project_root()),
            port=port,
            baudrate=baudrate,
        )
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return result if isinstance(result, dict) else {"lines": []}

    def _probe(project_path: str) -> dict[str, Any]:
        result = run_probe_project(
            project_path=str(cfg_manager.workspace_root() / (project_path or project)),
            config=cfg,
            project_root=str(cfg_manager.project_root()),
            probe_type=probe_type,
        )
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        return result if isinstance(result, dict) else {}

    return execute_runtime_loop(
        task=task,
        project=project,
        manager=cfg_manager,
        is_hardware_task=is_hardware_task,
        max_attempts=max_attempts,
        build_callback=_build,
        flash_callback=_flash,
        monitor_callback=_monitor,
        probe_callback=_probe,
    )


def explain_runtime_tool() -> dict[str, object]:
    return explain_runtime()
