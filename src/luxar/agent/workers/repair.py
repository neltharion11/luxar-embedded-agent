from __future__ import annotations

import re
from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient, LLMClientError


class RepairWorker:
    """
    A thin, domain-agnostic worker that repairs file contents using an LLM.
    It expects domain rules and priorities to be provided via the `skill_instructions` parameter.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm_client = LLMClient(config)
        self._configure_client()

    def _configure_client(self) -> None:
        review_cfg = self.config.review
        self.llm_client.timeout_sec = max(1, min(self.llm_client.timeout_sec, int(review_cfg.fix_timeout_sec)))
        self.llm_client.retry_attempts = max(1, min(self.llm_client.retry_attempts, int(review_cfg.fix_retry_attempts)))
        self.llm_client.max_tokens = max(256, min(self.llm_client.max_tokens, int(review_cfg.fix_max_tokens)))
        self.llm_client.thinking_enabled = bool(review_cfg.fix_thinking_enabled)
        if not self.llm_client.thinking_enabled:
            self.llm_client.thinking_effort = "medium"

    def repair_file(
        self,
        file_path: str,
        context_report: str,
        skill_instructions: str = "",
        apply_changes: bool = True,
    ) -> dict[str, object]:
        target = Path(file_path).resolve()
        
        if not target.exists():
            return {"success": False, "error": f"Target file not found: {target}"}

        source = target.read_text(encoding="utf-8")
        
        system_prompt = (
            "You are a file repair worker in the LUXAR 0.2.2 runtime.\n"
            "Apply minimal, conservative changes based on the provided evidence.\n"
            "Do not refactor unrelated parts. Do not invent missing headers if evidence is insufficient.\n"
            "Output the ENTIRE fixed file inside a markdown code block without any conversational text."
        )

        user_prompt = f"""
Please repair the following file based on the context and skill instructions.

【Original Content】
```{target.suffix.lstrip('.') or 'text'}
{source}
```

【Context / Errors】
{context_report}

【Skill Instructions】
{skill_instructions}
"""

        try:
            response = self.llm_client.complete(
                prompt=user_prompt.strip(),
                system_prompt=system_prompt,
            )
        except (LLMClientError, Exception) as exc:
            return {"success": False, "error": str(exc)}
            
        try:
            fixed_code = self._extract_code_block(response.content)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "raw_response": response.content}

        if apply_changes:
            target.write_text(fixed_code.rstrip() + "\n", encoding="utf-8")

        return {"success": True, "file_path": str(target), "applied": apply_changes}

    def _extract_code_block(self, content: str) -> str:
        match = re.search(r"```[^\n]*\n(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if not content.strip():
            raise ValueError("LLM response did not contain fixed code.")
        raise ValueError("LLM response did not contain a fenced code block.")
