from __future__ import annotations

import json
from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient, LLMClientError
from luxar.models.schemas import SkillArtifact
from luxar.prompts.skill_evolution import (
    SKILL_EVOLUTION_PROMPT,
    SKILL_EVOLUTION_SYSTEM_PROMPT,
)


class SkillManager:
    def __init__(self, config: AgentConfig, project_root: str):
        self.config = config
        self.project_root = Path(project_root).resolve()
        self.skill_root = (self.project_root / self.config.agent.skill_library / "protocols").resolve()
        self.skill_root.mkdir(parents=True, exist_ok=True)

    def should_update_protocol_skill(
        self,
        review_passed: bool,
        build_success: bool,
        project_success: bool,
    ) -> bool:
        if not self.config.evolution.enabled:
            return False
        if not review_passed:
            return False
        if self.config.evolution.require_project_success and not project_success:
            return False
        return build_success

    def update_protocol_skill(
        self,
        protocol: str,
        device_name: str,
        summary: str,
        lessons_learned: list[str],
        platforms: list[str],
        runtimes: list[str],
        source_project: str,
    ) -> SkillArtifact:
        normalized_protocol = (protocol or "generic").strip().lower()
        protocol_dir = self.skill_root / normalized_protocol
        protocol_dir.mkdir(parents=True, exist_ok=True)
        skill_path = protocol_dir / "SKILL.md"
        metadata_path = protocol_dir / "metadata.json"

        previous = self._load_existing_metadata(metadata_path)
        source_projects = self._merge_unique(previous.get("source_projects", []), [source_project])
        merged_platforms = self._merge_unique(previous.get("platforms", []), platforms)
        merged_runtimes = self._merge_unique(previous.get("runtimes", []), runtimes)
        validation_count = max(int(previous.get("validation_count", 0)), 0) + 1

        generated = self._generate_skill_markdown(
            protocol=normalized_protocol,
            device_name=device_name,
            summary=summary,
            lessons_learned=lessons_learned,
            platforms=merged_platforms,
            runtimes=merged_runtimes,
            source_project=source_project,
            existing_content=skill_path.read_text(encoding="utf-8") if skill_path.exists() else "",
        )
        skill_path.write_text(generated.rstrip() + "\n", encoding="utf-8")

        artifact = SkillArtifact(
            name=f"{normalized_protocol}_protocol_skill",
            protocol=normalized_protocol,
            path=str(skill_path),
            platforms=merged_platforms,
            runtimes=merged_runtimes,
            source_projects=source_projects,
            validation_count=validation_count,
        )
        metadata_path.write_text(
            json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return artifact

    def _generate_skill_markdown(
        self,
        protocol: str,
        device_name: str,
        summary: str,
        lessons_learned: list[str],
        platforms: list[str],
        runtimes: list[str],
        source_project: str,
        existing_content: str = "",
    ) -> str:
        prompt = SKILL_EVOLUTION_PROMPT.format(
            protocol=protocol.upper(),
            device_name=device_name,
            platforms=", ".join(platforms) or "unknown",
            runtimes=", ".join(runtimes) or "unknown",
            source_project=source_project,
            summary=summary.strip(),
            lessons_learned="\n".join(f"- {item}" for item in lessons_learned) or "- No explicit lessons recorded.",
        )
        try:
            response = LLMClient(self.config).complete(
                prompt=prompt,
                system_prompt=SKILL_EVOLUTION_SYSTEM_PROMPT,
            )
            content = response.content.strip()
            if content:
                return content
        except LLMClientError:
            pass
        return self._build_template_skill(
            protocol=protocol,
            device_name=device_name,
            summary=summary,
            lessons_learned=lessons_learned,
            platforms=platforms,
            runtimes=runtimes,
            source_project=source_project,
            existing_content=existing_content,
        )

    def _build_template_skill(
        self,
        protocol: str,
        device_name: str,
        summary: str,
        lessons_learned: list[str],
        platforms: list[str],
        runtimes: list[str],
        source_project: str,
        existing_content: str = "",
    ) -> str:
        observations = lessons_learned or [
            "Use injected HAL-style callbacks rather than platform globals.",
            "Preserve null checks and deterministic error returns in public APIs.",
            "Keep protocol timing and reset sequencing explicit in init flows.",
        ]
        previous_note = ""
        if existing_content.strip():
            previous_note = "## Existing Notes\n\nPrevious skill content was retained and should be merged carefully in future refinements.\n"
        return f"""# {protocol.upper()} Protocol Skill

## Scope

This skill captures reusable guidance for building and reviewing {protocol.upper()}-based embedded drivers. It should stay protocol-level and avoid board-specific private details.

## Applicability

- Protocol: {protocol.upper()}
- Validated device example: {device_name}
- Platforms: {", ".join(platforms) or "unknown"}
- Runtimes: {", ".join(runtimes) or "unknown"}
- Validation source: {source_project}

## Interface Pattern

- Prefer MCU-agnostic drivers with injected HAL callbacks.
- Keep transport operations explicit and timeout-aware.
- Return deterministic status codes from public APIs.
- Avoid direct references to CubeMX globals, dynamic allocation, and console I/O in the driver layer.

## Summary

{summary.strip() or "No summary provided."}

## Common Errors

- Missing null checks on public pointer parameters.
- Hardcoded platform handles or register constants leaking into reusable drivers.
- Blocking calls inside interrupt context.
- Missing reset, chip-select, or timing guard steps during initialization.

## Debug Checklist

- Confirm bus mode, frequency, and timing before first transaction.
- Validate reset and bring-up sequencing from the datasheet.
- Read a stable identity or status register before enabling advanced features.
- Keep logging and diagnostic hooks above the pure driver layer.

## Boundary Conditions

{chr(10).join(f"- {item}" for item in observations)}

{previous_note}""".strip()

    def _merge_unique(self, left: list[str], right: list[str]) -> list[str]:
        merged: list[str] = []
        for item in [*(left or []), *(right or [])]:
            normalized = str(item).strip()
            if normalized and normalized not in merged:
                merged.append(normalized)
        return merged

    def list_skills(self, protocol: str | None = None) -> list[dict]:
        skills: list[dict] = []
        for protocol_dir in sorted(self.skill_root.iterdir()):
            if not protocol_dir.is_dir():
                continue
            metadata_path = protocol_dir / "metadata.json"
            skill_path = protocol_dir / "SKILL.md"
            if not metadata_path.exists():
                continue
            meta = self._load_existing_metadata(metadata_path)
            proto = meta.get("protocol", "").lower()
            if protocol and proto != protocol.lower().strip():
                continue
            skills.append({
                "protocol": proto,
                "path": str(skill_path) if skill_path.exists() else "",
                "platforms": meta.get("platforms", []),
                "runtimes": meta.get("runtimes", []),
                "source_projects": meta.get("source_projects", []),
                "validation_count": meta.get("validation_count", 0),
                "updated_at": meta.get("updated_at", ""),
            })
        return skills

    def _load_existing_metadata(self, metadata_path: Path) -> dict:
        if not metadata_path.exists():
            return {}
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

