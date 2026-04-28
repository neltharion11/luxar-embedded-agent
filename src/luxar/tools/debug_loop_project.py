from __future__ import annotations

from luxar.core.config_manager import AgentConfig
from luxar.core.debug_loop import DebugLoop


def run_debug_loop_project(
    project_path: str,
    config: AgentConfig,
    project_root: str,
    probe: str | None = None,
    port: str = "",
    clean: bool = False,
    lines: int = 10,
    baudrate: int | None = None,
):
    debug_loop = DebugLoop(config=config, project_root=project_root)
    return debug_loop.run(
        project_path=project_path,
        probe=probe,
        port=port,
        clean=clean,
        lines=lines,
        baudrate=baudrate,
    )


