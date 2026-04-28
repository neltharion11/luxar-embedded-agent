from __future__ import annotations

import json
import re
from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient
from luxar.core.review_engine import ReviewEngine
from luxar.models.schemas import CodeFixResult, ReviewReport
from luxar.prompts.fix_code import FIX_CODE_PROMPT, FIX_CODE_SYSTEM_PROMPT


class CodeFixer:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm_client = LLMClient(config)

    def fix_file(
        self,
        project_path: str,
        file_path: str,
        review_report: ReviewReport | None = None,
        apply_changes: bool = True,
    ) -> CodeFixResult:
        project_root = Path(project_path).resolve()
        target = Path(file_path)
        if not target.is_absolute():
            target = project_root / target
        target = target.resolve()

        source = target.read_text(encoding="utf-8")
        report = review_report or ReviewEngine(str(project_root)).review_file(str(target))
        if report.passed:
            return CodeFixResult(
                success=True,
                file_path=str(target),
                applied=False,
                raw_response=source,
                review_report=report,
            )

        prompt = FIX_CODE_PROMPT.format(
            code=source,
            review_report=self._render_review_report(report),
        )
        response = self.llm_client.complete(
            prompt=prompt,
            system_prompt=FIX_CODE_SYSTEM_PROMPT,
        )
        fixed_code = self._extract_fixed_code(response.content)

        # VERIFICATION GATE: re-review to confirm fix resolved the issues
        if apply_changes:
            temp_path = target.with_suffix(target.suffix + ".fixed")
            temp_path.write_text(fixed_code.rstrip() + "\n", encoding="utf-8")
            re_review = ReviewEngine(str(project_root)).review_file(str(temp_path))
            temp_path.unlink(missing_ok=True)

            if (re_review.critical_count > 0 or re_review.error_count > 0):
                return CodeFixResult(
                    success=False,
                    file_path=str(target),
                    applied=False,
                    raw_response=response.content,
                    review_report=re_review,
                )

        if apply_changes:
            target.write_text(fixed_code.rstrip() + "\n", encoding="utf-8")

        return CodeFixResult(
            success=True,
            file_path=str(target),
            applied=apply_changes,
            raw_response=response.content,
            review_report=report,
        )

    def _render_review_report(self, report: ReviewReport) -> str:
        return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2)

    def _extract_fixed_code(self, content: str) -> str:
        match = re.search(r"```(?:c)?\n(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        if not content.strip():
            raise ValueError("LLM response did not contain fixed code.")
        return content.strip()

