from __future__ import annotations

import os
from pathlib import Path

import portalocker


class ProjectLock:
    """Cross-platform project lock."""

    def __init__(self, project_path: str):
        self.lock_file = Path(project_path) / ".agent.lock"
        self.fd = None

    def __enter__(self) -> "ProjectLock":
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.fd = self.lock_file.open("a+", encoding="utf-8")
        portalocker.lock(self.fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
        self.fd.seek(0)
        self.fd.truncate()
        self.fd.write(str(os.getpid()))
        self.fd.flush()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.fd is not None:
            portalocker.unlock(self.fd)
            self.fd.close()

