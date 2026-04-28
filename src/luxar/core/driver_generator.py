from __future__ import annotations

import re
from pathlib import Path

from luxar.core.asset_reuse import AssetReuseAdvisor
from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient
from luxar.models.schemas import DriverGenerationResult
from luxar.prompts.driver_generation import (
    DRIVER_GENERATION_PROMPT,
    DRIVER_GENERATION_SYSTEM_PROMPT,
)


class DriverGenerator:
    def __init__(self, config: AgentConfig, project_root: str | Path = "."):
        self.config = config
        self.project_root = Path(project_root).resolve()
        self.llm_client = LLMClient(config)
        self.reuse_advisor = AssetReuseAdvisor(
            project_root=self.project_root,
            driver_library_root=self.project_root / self.config.agent.driver_library,
            skill_library_root=self.project_root / self.config.agent.skill_library,
        )

    def generate_driver(
        self,
        chip: str,
        interface: str,
        protocol_summary: str,
        register_summary: str,
        output_dir: str,
        vendor: str = "",
        device: str = "",
        allow_reuse: bool = True,
        reuse_context: dict | None = None,
    ) -> DriverGenerationResult:
        chip_name = chip.strip()
        interface_name = interface.strip().upper()
        reuse_context = reuse_context or self.reuse_advisor.build_context(
            chip=chip_name,
            interface=interface_name,
            vendor=vendor,
            device=device,
            register_summary=register_summary,
        )
        reuse_candidate_payload = reuse_context.get("reuse_candidate")
        reuse_candidate = reuse_candidate_payload if isinstance(reuse_candidate_payload, dict) else None
        confidence = reuse_context.get("confidence", 0.0)
        resolved_output = Path(output_dir).resolve()
        stem = self._resolve_stem(chip_name, vendor=vendor, device=device)
        if allow_reuse and reuse_candidate:
            reused_result = self.reuse_existing_driver(
                chip=chip_name,
                interface=interface_name,
                output_dir=str(resolved_output),
                reuse_context=reuse_context,
                vendor=vendor,
                device=device,
            )
            if reused_result is not None:
                return reused_result

        if confidence < 0.5:
            reuse_qualifier = "Low confidence reuse candidate found, but falling back to generation."
        else:
            reuse_qualifier = f"Reuse candidate available with confidence {confidence:.2f}, but was not materialized."

        enriched_reuse_summary = (
            f"{reuse_context['summary']}\n\nconfidence: {confidence:.2f}\nreuse_qualifier: {reuse_qualifier}"
            if reuse_context.get("summary")
            else f"confidence: {confidence:.2f}\nreuse_qualifier: {reuse_qualifier}"
        )

        prompt = DRIVER_GENERATION_PROMPT.format(
            chip_name=chip_name,
            interface=interface_name,
            protocol_summary=protocol_summary.strip(),
            register_summary=register_summary.strip(),
            reuse_context=enriched_reuse_summary or "No relevant local assets were found.",
        )
        response = self.llm_client.complete(
            prompt=prompt,
            system_prompt=DRIVER_GENERATION_SYSTEM_PROMPT,
        )

        try:
            header_code, source_code = self._extract_code_blocks(response.content)
        except ValueError as exc:
            return DriverGenerationResult(
                success=False,
                chip=chip_name,
                interface=interface_name,
                output_dir=str(resolved_output),
                reuse_summary=reuse_context["summary"],
                reuse_sources=reuse_context["sources"],
                error=str(exc),
                raw_response=response.content,
            )

        resolved_output.mkdir(parents=True, exist_ok=True)

        header_path = resolved_output / f"{stem}.h"
        source_path = resolved_output / f"{stem}.c"
        header_path.write_text(header_code.rstrip() + "\n", encoding="utf-8")
        source_path.write_text(source_code.rstrip() + "\n", encoding="utf-8")

        return DriverGenerationResult(
            success=True,
            chip=chip_name,
            interface=interface_name,
            output_dir=str(resolved_output),
            header_path=str(header_path),
            source_path=str(source_path),
            reuse_summary=reuse_context["summary"],
            reuse_sources=reuse_context["sources"],
            raw_response=response.content,
        )

    def reuse_existing_driver(
        self,
        chip: str,
        interface: str,
        output_dir: str,
        reuse_context: dict,
        vendor: str = "",
        device: str = "",
    ) -> DriverGenerationResult | None:
        reuse_candidate_payload = reuse_context.get("reuse_candidate")
        if not isinstance(reuse_candidate_payload, dict):
            return None
        candidate_path = str(reuse_candidate_payload.get("path", ""))
        resolved_output = Path(output_dir).resolve()
        stem = self._resolve_stem(chip, vendor=vendor, device=device)
        try:
            header_path, source_path = self.reuse_advisor.materialize_reused_driver(
                candidate=self._metadata_from_payload(reuse_candidate_payload),
                output_dir=resolved_output,
                target_stem=stem,
            )
            self.reuse_advisor.driver_library.record_reuse(candidate_path)
        except FileNotFoundError:
            return None
        return DriverGenerationResult(
            success=True,
            chip=chip.strip(),
            interface=interface.strip().upper(),
            output_dir=str(resolved_output),
            header_path=header_path,
            source_path=source_path,
            reused_existing=True,
            reused_driver_path=candidate_path,
            reuse_summary=str(reuse_context.get("summary", "")),
            reuse_sources=list(reuse_context.get("sources", [])),
            raw_response=f"Reused existing reviewed driver from {candidate_path}",
        )

    def _extract_code_blocks(self, content: str) -> tuple[str, str]:
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        blocks = re.findall(r"```(?:c\s+header|c\s+source|c)\n(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
        if len(blocks) < 2:
            raise ValueError("LLM response did not include separate header/source code blocks.")
        return blocks[0].strip(), blocks[1].strip()

    def _resolve_stem(self, chip: str, vendor: str = "", device: str = "") -> str:
        base = device.strip() or chip.strip() or vendor.strip() or "generated_driver"
        stem = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_").lower()
        return stem or "generated_driver"

    def _metadata_from_payload(self, payload: dict):
        from luxar.models.schemas import DriverMetadata

        return DriverMetadata.model_validate(payload)

