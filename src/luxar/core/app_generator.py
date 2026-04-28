from __future__ import annotations

import re
from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient, LLMClientError
from luxar.models.schemas import AppGenerationResult, ProjectConfig, ProjectPlan
from luxar.prompts.app_generation import (
    APP_GENERATION_SYSTEM_PROMPT,
    build_app_generation_prompt,
)


class AppGenerator:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm_client = LLMClient(config)

    def generate_app(
        self,
        project: ProjectConfig,
        project_plan: ProjectPlan,
        installed_drivers: list[str] | None = None,
    ) -> AppGenerationResult:
        project_root = Path(project.path).resolve()
        header_path = project_root / "App" / "Inc" / "app_main.h"
        source_path = project_root / "App" / "Src" / "app_main.c"
        header_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.parent.mkdir(parents=True, exist_ok=True)

        prompt = build_app_generation_prompt(
            project_name=project.name,
            mcu=project.mcu,
            project_mode=project.project_mode,
            project_plan=project_plan,
            installed_drivers=installed_drivers,
        )

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                system_prompt=APP_GENERATION_SYSTEM_PROMPT,
            )
            header_code, source_code = self._extract_code_blocks(response.content)
            used_fallback = False
            raw_response = response.content
        except (LLMClientError, ValueError):
            header_code, source_code = self._fallback_code(project_plan, installed_drivers=installed_drivers or [])
            used_fallback = True
            raw_response = ""

        header_path.write_text(header_code.rstrip() + "\n", encoding="utf-8")
        source_path.write_text(source_code.rstrip() + "\n", encoding="utf-8")

        # VERIFICATION GATE: basic syntactic integrity check
        failures = self._verify_generated(header_code, source_code)
        return AppGenerationResult(
            success=len(failures) == 0,
            project=project.name,
            requirement=project_plan.requirement_summary,
            project_plan=project_plan,
            header_path=str(header_path),
            source_path=str(source_path),
            used_fallback=used_fallback,
            raw_response=raw_response,
            error="; ".join(failures) if failures else "",
        )

    def _extract_code_blocks(self, content: str) -> tuple[str, str]:
        matches = re.findall(r"```c\s+(header|source)\n(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
        blocks = {kind.lower(): body.strip() for kind, body in matches}
        if "header" in blocks and "source" in blocks:
            return blocks["header"], blocks["source"]

        generic = re.findall(r"```(?:c)?\n(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
        if len(generic) >= 2:
            return generic[0].strip(), generic[1].strip()
        raise ValueError("LLM response did not contain both header and source code blocks.")

    def _fallback_code(self, project_plan: ProjectPlan, installed_drivers: list[str]) -> tuple[str, str]:
        comment = project_plan.requirement_summary[:160] if project_plan.requirement_summary else "Application requirement placeholder."
        driver_comment = ", ".join(installed_drivers) or "none"
        feature_comments = "\n".join(f" * - {item}" for item in project_plan.features) or " * - none"
        doc_comment = project_plan.document_context_summary[:240] if project_plan.document_context_summary else "none"
        action_comments = "\n".join(f" * TODO: {item}" for item in project_plan.cubemx_or_firmware_actions) or " * TODO: review missing hardware configuration."
        header = """#ifndef APP_MAIN_H
#define APP_MAIN_H

/**
 * @brief Initialize application-level resources.
 */
void app_main_init(void);

/**
 * @brief Run one iteration of the application loop.
 */
void app_main_loop(void);

#endif /* APP_MAIN_H */
"""
        source = f"""#include "app_main.h"

/* Requirement summary: {comment} */
/* Installed drivers: {driver_comment} */
/* Document context: {doc_comment} */

/**
 * Planned features:
{feature_comments}
 */

/**
 * Pending hardware integration actions:
{action_comments}
 */

void app_main_init(void)
{{
    /* TODO(luxar): integrate any required HAL hooks after completing the pending configuration actions. */
}}

void app_main_loop(void)
{{
    /* TODO(luxar): implement the planned application behavior without guessing unknown pins or peripheral instances. */
}}
"""
        return header, source

    def _verify_generated(self, header: str, source: str) -> list[str]:
        """Run integrity checks on generated code. Returns list of failure messages."""
        failures: list[str] = []
        if "app_main_init" not in header:
            failures.append("MISSING: app_main_init declaration in header")
        if "app_main_loop" not in header:
            failures.append("MISSING: app_main_loop declaration in header")
        if "app_main_init" not in source:
            failures.append("MISSING: app_main_init implementation in source")
        if "app_main_loop" not in source:
            failures.append("MISSING: app_main_loop implementation in source")
        if "malloc" in source or "malloc" in header:
            failures.append("FORBIDDEN: malloc usage detected")
        if "printf" in source:
            failures.append("WARNING: printf usage — only allowed if UART was explicitly required")
        if "#ifndef" not in header and "#ifndef" not in header.upper():
            failures.append("MISSING: include guard in header")
        return failures
