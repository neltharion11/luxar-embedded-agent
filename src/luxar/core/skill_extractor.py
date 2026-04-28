"""Auto-extract reusable skills from successful conversations."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """\
Based on the workflow result below, decide whether new reusable embedded development knowledge was gained.

If YES:
Return a JSON object with:
  - "should_store": true
  - "protocol": the protocol used (spi, i2c, uart, gpio, etc.)
  - "device": the chip/device name
  - "summary": concise summary of what was learned (1-3 sentences)
  - "lessons": list of 1-3 key lessons learned

If NO (nothing new was learned):
Return: {"should_store": false}

Workflow result:
{result}

Conversation summary:
{conversation}

Return ONLY the JSON, no other text."""


class SkillExtractor:
    def __init__(self, skill_library_root: str | Path):
        self.skill_root = Path(skill_library_root) / "protocols"

    def should_extract(self, workflow_result: dict) -> bool:
        """Check if the workflow succeeded and had enough meaningful steps."""
        if not workflow_result.get("success", False):
            return False
        steps = (workflow_result.get("workflow", {}) or {}).get("steps", [])
        completed = [s for s in steps if s.get("status") == "completed"]
        return len(completed) >= 3

    def extract(self, conversation: str, result: dict, client: Any) -> dict | None:
        """Ask LLM to extract reusable knowledge from the conversation."""
        prompt = EXTRACT_PROMPT.format(
            result=json.dumps(result, ensure_ascii=False, indent=2),
            conversation=conversation[-3000:],
        )
        try:
            resp = client.complete(prompt=prompt, system_prompt="")
            content = resp.content if hasattr(resp, "content") else str(resp)
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("\n```", 1)[0]
            data = json.loads(content)
        except Exception as e:
            logger.warning("Skill extraction failed: %s", e)
            return None

        if not data.get("should_store"):
            return None
        return data

    def save_skill(self, data: dict, source_project: str):
        device = data.get("device", "unknown").replace("/", "_")
        path = self.skill_root / f"{device}.md"
        self.skill_root.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        protocol = data.get("protocol", "general")
        summary = data.get("summary", "")
        lessons = data.get("lessons", [])

        content = f"""# {device} ({protocol.upper()})
- **Protocol**: {protocol.upper()}
- **Source project**: {source_project}
- **Extracted**: {timestamp}
- **Summary**: {summary}
"""
        for l in lessons:
            content += f"- **Lesson**: {l}\n"

        path.write_text(content, encoding="utf-8")
        logger.info("Skill saved: %s", path)
        return str(path)
