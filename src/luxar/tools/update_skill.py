from __future__ import annotations

from luxar.core.config_manager import AgentConfig
from luxar.core.skill_manager import SkillManager


def run_update_skill(
    config: AgentConfig,
    project_root: str,
    protocol: str,
    device_name: str,
    summary: str,
    lessons_learned: list[str],
    platforms: list[str],
    runtimes: list[str],
    source_project: str,
):
    manager = SkillManager(config=config, project_root=project_root)
    return manager.update_protocol_skill(
        protocol=protocol,
        device_name=device_name,
        summary=summary,
        lessons_learned=lessons_learned,
        platforms=platforms,
        runtimes=runtimes,
        source_project=source_project,
    )

