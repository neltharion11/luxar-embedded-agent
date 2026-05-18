from __future__ import annotations

from luxar.agent.context_builder import RuntimeWorkspace
from luxar.core.config_manager import ConfigManager
from luxar.tools.build_project import run_build_project
from luxar.tools.flash_project import run_flash_project
from luxar.tools.monitor_project import run_monitor_project


def workspace_inspect() -> dict[str, object]:
    workspace = RuntimeWorkspace.from_manager(ConfigManager())
    return {"success": True, "workspace": workspace.snapshot()}


def workspace_build(project: str, clean: bool = False) -> object:
    cfg_manager = ConfigManager()
    cfg = cfg_manager.ensure_default_config()
    return run_build_project(config=cfg, workspace_root=str(cfg_manager.workspace_root()), project_name=project, clean=clean)


def workspace_flash(project: str, probe: str = "") -> object:
    cfg_manager = ConfigManager()
    cfg = cfg_manager.ensure_default_config()
    return run_flash_project(config=cfg, workspace_root=str(cfg_manager.workspace_root()), project_name=project, probe=probe or None)


def workspace_monitor(project: str, port: str, baudrate: int = 115200) -> object:
    cfg_manager = ConfigManager()
    cfg = cfg_manager.ensure_default_config()
    return run_monitor_project(config=cfg, workspace_root=str(cfg_manager.workspace_root()), project_name=project, port=port, baudrate=baudrate)


def workspace_probe(project: str, probe_type: str = "i2c") -> dict[str, object]:
    return {
        "success": True,
        "project": project,
        "probe_type": probe_type,
        "status": "planned",
        "message": "Workspace probe is a runtime primitive placeholder and should be backed by a concrete worker.",
    }
