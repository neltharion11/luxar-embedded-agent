from __future__ import annotations

import json
import re

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient, LLMClientError
from luxar.core.mcu_reference import get_mcu_pin_map, format_mcu_pin_reference
from luxar.models.schemas import EngineeringContext, ProjectConfig, ProjectPlan


class PlannerWorker:
    """A thin worker for extracting a structured project plan via LLM.

    It has no hardcoded fallback rules or regex inference. It strictly delegates
    planning to the LLM based on the provided skill instructions.
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm_client = LLMClient(config)

    def build_plan(
        self,
        *,
        project: ProjectConfig,
        requirement: str,
        skill_instructions: str,
        document_context: str = "",
        engineering_context: EngineeringContext | None = None,
    ) -> ProjectPlan:
        doc_section = document_context.strip() or "No additional document context was provided."
        mcu_reference = format_mcu_pin_reference(project.mcu)

        prompt = (
            f"{skill_instructions}\n\n"
            f"[Project]\n"
            f"- Name: {project.name}\n"
            f"- MCU: {project.mcu}\n"
            f"- Project mode: {project.project_mode}\n\n"
            f"[Requirement]\n"
            f"{requirement}\n\n"
            f"[Document context]\n"
            f"{doc_section}\n\n"
            f"{mcu_reference}\n"
        )

        try:
            response = self.llm_client.complete(prompt=prompt)
            payload = self._extract_json_payload(response.content)
            plan = ProjectPlan.model_validate(payload)
            plan = plan.model_copy(
                update={
                    "used_fallback": False,
                    "raw_response": response.content,
                    "engineering_context": engineering_context,
                }
            )
            return plan
        except (LLMClientError, ValueError, json.JSONDecodeError) as e:
            # Mechanical fallback if the LLM utterly fails to produce JSON schema
            return ProjectPlan(
                requirement_summary=requirement[:220],
                features=["Implement the user-requested behavior conservatively."],
                needed_drivers=[],
                peripheral_hints=["Review hardware requirement due to planning failure."],
                cubemx_or_firmware_actions=["Review the requirement and configure hardware manually."],
                app_behavior_summary="Application layer should execute the requested behavior.",
                document_context_summary=document_context.strip()[:1200],
                engineering_context=engineering_context,
                risk_notes=[f"Planner Worker failed to generate a valid plan: {str(e)}"],
                used_fallback=True,
                raw_response=getattr(e, "content", ""),
            )

    def _extract_json_payload(self, content: str) -> dict:
        cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        candidate = cleaned.strip()
        fenced = re.search(r"```(?:json)?\n(.*?)```", candidate, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            candidate = fenced.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise ValueError("No JSON payload found in planning response.")
            return json.loads(candidate[start : end + 1])
