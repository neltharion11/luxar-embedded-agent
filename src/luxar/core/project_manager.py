from __future__ import annotations

import json
from pathlib import Path

from luxar.models.schemas import ProjectConfig


DEFAULT_CLANG_TIDY = """Checks: >
  clang-analyzer-*,
  bugprone-*,
  portability-*,
  readability-*,
  -readability-magic-numbers

WarningsAsErrors: ''

CheckOptions:
  - key: readability-identifier-naming.FunctionCase
    value: lower_case
  - key: readability-identifier-naming.VariableCase
    value: lower_case
  - key: readability-identifier-naming.MacroDefinitionCase
    value: UPPER_CASE
"""


class ProjectManager:
    def __init__(self, workspace: str):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def create_project(
        self,
        name: str,
        mcu: str,
        platform: str = "stm32cubemx",
        runtime: str = "baremetal",
        project_mode: str = "cubemx",
        firmware_package: str = "",
    ) -> ProjectConfig:
        project_dir = self.workspace / name
        project_dir.mkdir(parents=True, exist_ok=True)

        for rel_dir in ("App", "App/Inc", "App/Src", "logs"):
            (project_dir / rel_dir).mkdir(parents=True, exist_ok=True)

        ioc_file = project_dir / f"{name}.ioc"
        if not ioc_file.exists():
            ioc_file.write_text(
                f"# Placeholder CubeMX project file for {name}\n",
                encoding="utf-8",
            )

        clang_tidy = project_dir / ".clang-tidy"
        if not clang_tidy.exists():
            clang_tidy.write_text(DEFAULT_CLANG_TIDY, encoding="utf-8")

        config = ProjectConfig(
            name=name,
            path=str(project_dir.resolve()),
            platform=platform,
            runtime=runtime,
            project_mode=project_mode,
            mcu=mcu,
            ioc_file=str(ioc_file.resolve()),
            firmware_package=firmware_package,
        )
        self._write_project_metadata(project_dir, config)
        return config

    def load_project(self, name: str) -> ProjectConfig:
        project_dir = self.workspace / name
        metadata = project_dir / ".agent_project.json"
        if not metadata.exists():
            raise FileNotFoundError(f"Project metadata not found: {metadata}")
        return ProjectConfig.model_validate_json(metadata.read_text(encoding="utf-8"))

    def import_project(
        self,
        *,
        source_path: str,
        name: str | None = None,
        mcu: str = "",
        platform: str = "stm32cubemx",
        runtime: str = "baremetal",
        project_mode: str = "cubemx",
        firmware_package: str = "",
    ) -> ProjectConfig:
        source_dir = Path(source_path).resolve()
        if not source_dir.exists() or not source_dir.is_dir():
            raise FileNotFoundError(f"Project directory not found: {source_dir}")

        entry_name = (name or source_dir.name).strip()
        entry_dir = self.workspace / entry_name
        entry_dir.mkdir(parents=True, exist_ok=True)

        metadata_file = source_dir / ".agent_project.json"
        if metadata_file.exists():
            loaded = ProjectConfig.model_validate_json(metadata_file.read_text(encoding="utf-8"))
            config = loaded.model_copy(
                update={
                    "name": entry_name,
                    "path": str(source_dir),
                    "mcu": loaded.mcu or mcu,
                    "platform": loaded.platform or platform,
                    "runtime": loaded.runtime or runtime,
                    "project_mode": loaded.project_mode or project_mode,
                    "firmware_package": loaded.firmware_package or firmware_package,
                }
            )
        else:
            ioc_candidates = sorted(source_dir.glob("*.ioc"))
            ioc_file = ioc_candidates[0] if ioc_candidates else source_dir / f"{entry_name}.ioc"
            config = ProjectConfig(
                name=entry_name,
                path=str(source_dir),
                platform=platform,
                runtime=runtime,
                project_mode=project_mode,
                mcu=mcu or "UNKNOWN",
                ioc_file=str(ioc_file.resolve()),
                firmware_package=firmware_package,
            )

        self._write_project_metadata(entry_dir, config)
        return config

    def _write_project_metadata(self, project_dir: Path, config: ProjectConfig) -> None:
        metadata_path = project_dir / ".agent_project.json"
        metadata_path.write_text(
            json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


