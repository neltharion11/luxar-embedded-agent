from __future__ import annotations

import re
from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient


class GeneratorWorker:
    """A thin worker for generating paired files (e.g., C header and source).

    It has no domain knowledge. It only applies skill instructions, calls the LLM,
    extracts code blocks, and writes them to disk.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm_client = LLMClient(config)

    def generate_files(
        self,
        context_data: dict,
        skill_instructions: str,
        output_dir: str,
        stem: str,
    ) -> dict:
        """Generates paired header and source files based on context and skill instructions.
        
        Returns a dict with 'success', 'header_path', 'source_path', 'error', 'raw_response'.
        """
        resolved_output = Path(output_dir).resolve()
        
        # Build prompt mechanically
        context_str = "\n".join(f"- {k}: {v}" for k, v in context_data.items() if v)
        prompt = (
            f"{skill_instructions}\n\n"
            f"Context Data:\n{context_str}\n\n"
            "Please output the files using the standard Markdown code blocks format as instructed."
        )

        response = self.llm_client.complete(prompt=prompt)
        raw_response = response.content

        try:
            header_code, source_code = self._extract_code_blocks(raw_response)
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
                "raw_response": raw_response,
            }

        resolved_output.mkdir(parents=True, exist_ok=True)
        header_path = resolved_output / f"{stem}.h"
        source_path = resolved_output / f"{stem}.c"

        header_path.write_text(header_code.rstrip() + "\n", encoding="utf-8")
        source_path.write_text(source_code.rstrip() + "\n", encoding="utf-8")

        return {
            "success": True,
            "header_path": str(header_path),
            "source_path": str(source_path),
            "raw_response": raw_response,
        }

    def _extract_code_blocks(self, content: str) -> tuple[str, str]:
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        blocks = re.findall(r"```(?:c\s+header|c\s+source|c)\n(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
        if len(blocks) < 2:
            raise ValueError("LLM response did not include separate header/source code blocks.")
        return blocks[0].strip(), blocks[1].strip()
