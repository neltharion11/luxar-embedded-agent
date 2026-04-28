from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


class AgentLogger:
    def __init__(self, log_dir: str = "./agent_workspace/logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.event_log = self.log_dir / "events.jsonl"

        self.logger = logging.getLogger("Luxar")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        file_handler = logging.FileHandler(
            self.log_dir / f"agent_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
        if not self.logger.handlers:
            self.logger.addHandler(file_handler)

    def log_event(self, event_type: str, project: str, details: dict[str, Any]) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "project": project,
            "details": details,
        }
        with self.event_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.logger.info("[%s] %s: %s", event_type, project, details)



