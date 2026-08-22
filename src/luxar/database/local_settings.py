"""Settings for the default embedded SQLite + LanceDB storage profile."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class LocalStorageSettings(BaseSettings):
    """Resolve all durable local data below one configurable directory."""

    model_config = SettingsConfigDict(
        env_prefix="LUXAR_STORAGE_",
        extra="ignore",
    )

    directory: Path = Path(".luxar-data")
    application_filename: str = "luxar.sqlite3"
    checkpoint_filename: str = "checkpoints.sqlite3"
    knowledge_directory_name: str = "knowledge.lance"
    sdk_knowledge_directory_name: str = "sdk-knowledge.lance"

    @classmethod
    def for_projects_root(cls, projects_root: Path) -> "LocalStorageSettings":
        """Use an explicit setting or a stable directory beside projects/."""

        resolved_projects_root = projects_root.expanduser().resolve()
        if "LUXAR_STORAGE_DIRECTORY" in os.environ:
            configured = cls()
            if configured.directory.is_absolute():
                return configured
            return cls(
                directory=(
                    resolved_projects_root.parent / configured.directory
                )
            )
        return cls(directory=resolved_projects_root.parent / ".luxar-data")

    @property
    def root(self) -> Path:
        return self.directory.expanduser().resolve()

    @property
    def application_path(self) -> Path:
        return self.root / self.application_filename

    @property
    def checkpoint_path(self) -> Path:
        return self.root / self.checkpoint_filename

    @property
    def knowledge_path(self) -> Path:
        return self.root / self.knowledge_directory_name

    @property
    def sdk_knowledge_path(self) -> Path:
        return self.root / self.sdk_knowledge_directory_name
