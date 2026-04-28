from __future__ import annotations

from luxar.core.project_manager import ProjectManager
from luxar.models.schemas import ProjectConfig


def run_init_project(
    workspace: str,
    name: str,
    mcu: str,
    platform: str,
    runtime: str,
    project_mode: str,
    firmware_package: str,
) -> ProjectConfig:
    manager = ProjectManager(workspace)
    return manager.create_project(
        name=name,
        mcu=mcu,
        platform=platform,
        runtime=runtime,
        project_mode=project_mode,
        firmware_package=firmware_package,
    )


