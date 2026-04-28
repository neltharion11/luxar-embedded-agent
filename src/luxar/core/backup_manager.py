from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


class BackupManager:
    def __init__(self, project_path: str):
        self.project = Path(project_path)
        self.backup_dir = self.project / ".agent_backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_snapshot(self, label: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_path = self.backup_dir / f"{timestamp}_{label}"
        snapshot_path.mkdir(parents=True, exist_ok=False)

        for item_name in ("App", "CMakeLists.txt"):
            src = self.project / item_name
            if not src.exists():
                continue
            dst = snapshot_path / item_name
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        for ioc_file in self.project.glob("*.ioc"):
            shutil.copy2(ioc_file, snapshot_path / ioc_file.name)

        return snapshot_path

    def restore_snapshot(self, snapshot_path: str | Path) -> None:
        snapshot = Path(snapshot_path)
        for target_name in ("App", "CMakeLists.txt"):
            dst = self.project / target_name
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()

        for ioc_file in self.project.glob("*.ioc"):
            ioc_file.unlink()

        for item in snapshot.iterdir():
            dst = self.project / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)

    def list_snapshots(self) -> list[Path]:
        return sorted(self.backup_dir.iterdir(), key=lambda path: path.name)

