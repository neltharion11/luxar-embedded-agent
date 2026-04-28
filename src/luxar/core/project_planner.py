from __future__ import annotations

import json
import re

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient, LLMClientError
from luxar.models.schemas import DriverRequirement, EngineeringContext, ProjectConfig, ProjectPlan
from luxar.prompts.project_planning import (
    PROJECT_PLANNING_SYSTEM_PROMPT,
    build_project_planning_prompt,
)


class ProjectPlanner:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm_client = LLMClient(config)

    def build_plan(
        self,
        *,
        project: ProjectConfig,
        requirement: str,
        document_context: str = "",
        engineering_context: EngineeringContext | None = None,
    ) -> ProjectPlan:
        prompt = build_project_planning_prompt(
            project_name=project.name,
            mcu=project.mcu,
            project_mode=project.project_mode,
            requirement=requirement,
            document_context=document_context,
        )
        try:
            response = self.llm_client.complete(
                prompt=prompt,
                system_prompt=PROJECT_PLANNING_SYSTEM_PROMPT,
            )
            payload = self._extract_json_payload(response.content)
            plan = ProjectPlan.model_validate(payload)
            return plan.model_copy(update={"used_fallback": False, "raw_response": response.content, "engineering_context": engineering_context})
        except (LLMClientError, ValueError, json.JSONDecodeError):
            return self._fallback_plan(project=project, requirement=requirement, document_context=document_context, engineering_context=engineering_context)

    def _fallback_plan(self, *, project: ProjectConfig, requirement: str, document_context: str = "", engineering_context: EngineeringContext | None = None) -> ProjectPlan:
        text = " ".join(requirement.strip().split())
        lowered = text.lower()
        features: list[str] = []
        peripheral_hints: list[str] = []
        actions: list[str] = []
        risks: list[str] = []
        needed_drivers: list[DriverRequirement] = []

        blink = "blink" in lowered or "led" in lowered
        uart_log = any(token in lowered for token in ("uart", "serial", "print", "log", "hello"))
        periodic = any(token in lowered for token in ("every", "periodic", "per second", "1hz", "once per second"))
        polling = "poll" in lowered or "polling" in lowered

        if blink:
            features.append("Blink an LED from the application loop.")
            peripheral_hints.append("GPIO output required for an LED indicator.")
            if project.project_mode == "cubemx":
                actions.append("Configure one GPIO pin as an output for the LED in CubeMX and document the label used by App code.")
            else:
                actions.append("Wire one LED GPIO output into the firmware project and expose its HAL integration point to App code.")
            risks.append("LED pin is not specified; application code must keep GPIO integration as a TODO instead of guessing a pin.")

        if uart_log:
            features.append("Emit UART log output from the application layer.")
            peripheral_hints.append("UART TX path is required for textual status output.")
            if project.project_mode == "cubemx":
                actions.append("Enable one USART/UART peripheral for TX in CubeMX and keep the selected instance available to App integration code.")
            else:
                actions.append("Provide a UART transmit hook from the firmware project without hardcoding an unknown UART instance in App code.")
            risks.append("UART instance and pins are not specified; logging must use a TODO-based integration point.")

        if periodic:
            features.append("Run periodic behavior based on a fixed cadence.")
        elif polling:
            features.append("Use polling-style application behavior.")

        driver_mentions = self._detect_driver_mentions(text)
        for chip_name, interface in driver_mentions:
            needed_drivers.append(
                DriverRequirement(
                    chip=chip_name,
                    interface=interface,
                    device=chip_name.lower(),
                    confidence=0.7,
                    rationale=f"Detected external device mention '{chip_name}' with {interface} protocol context.",
                )
            )
            peripheral_hints.append(f"{interface} peripheral is required to communicate with {chip_name}.")
            if project.project_mode == "cubemx":
                actions.append(f"Configure one {interface} peripheral in CubeMX for the {chip_name} device and verify bus timing and pins.")
            else:
                actions.append(f"Integrate a HAL-facing {interface} transport for the {chip_name} device without inventing unspecified pins.")
            if polling:
                features.append(f"Poll {chip_name} from the main loop.")
            else:
                features.append(f"Initialize and interact with {chip_name} from the application layer.")
            risks.append(f"{chip_name} bus pins, bus index, and timing values are not fully specified; keep transport integration explicit.")

        if not needed_drivers:
            interface = self._detect_interface(lowered)
            chip_name = self._detect_chip_name(text)
            if chip_name and interface:
                needed_drivers.append(
                    DriverRequirement(
                        chip=chip_name,
                        interface=interface,
                        device=chip_name.lower(),
                        confidence=0.6,
                        rationale=f"Detected external device mention '{chip_name}' with {interface} protocol context.",
                    )
                )
                peripheral_hints.append(f"{interface} peripheral is required to communicate with {chip_name}.")
                if project.project_mode == "cubemx":
                    actions.append(f"Configure one {interface} peripheral in CubeMX for the {chip_name} device and verify bus timing and pins.")
                else:
                    actions.append(f"Integrate a HAL-facing {interface} transport for the {chip_name} device without inventing unspecified pins.")
                if polling:
                    features.append(f"Poll {chip_name} from the main loop.")
                else:
                    features.append(f"Initialize and interact with {chip_name} from the application layer.")

        if not features:
            features.append("Implement the user-requested application behavior conservatively in App code.")
            risks.append("Requirement did not map cleanly to a known hardware pattern; manual configuration review is recommended.")
        if not actions:
            actions.append("Review the requirement and complete any missing CubeMX or firmware peripheral configuration before flashing.")

        summary = text[:220] if text else f"Plan application behavior for {project.name}."
        behavior = self._build_behavior_summary(features, periodic=periodic, polling=polling)
        return ProjectPlan(
            requirement_summary=summary,
            features=self._dedupe(features),
            needed_drivers=needed_drivers,
            peripheral_hints=self._dedupe(peripheral_hints),
            cubemx_or_firmware_actions=self._dedupe(actions),
            app_behavior_summary=behavior,
            document_context_summary=document_context.strip()[:1200],
            engineering_context=engineering_context,
            risk_notes=self._dedupe(risks),
            used_fallback=True,
            raw_response="",
        )

    def _extract_json_payload(self, content: str) -> dict:
        candidate = content.strip()
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
            return json.loads(candidate[start:end + 1])

    def _detect_interface(self, lowered: str) -> str:
        if "spi" in lowered:
            return "SPI"
        if "i2c" in lowered or "iic" in lowered:
            return "I2C"
        if "uart" in lowered or "serial" in lowered:
            return "UART"
        return ""

    def _detect_chip_name(self, text: str) -> str:
        for match in re.findall(r"\b[A-Z]{2,}[A-Z0-9_-]*\d[A-Z0-9_-]*\b", text):
            token = match.strip()
            if token.startswith("STM32"):
                continue
            return token
        sensor_match = re.search(r"\b([A-Za-z]+)\s+sensor\b", text, flags=re.IGNORECASE)
        if sensor_match:
            return sensor_match.group(1).upper()
        return ""

    def _build_behavior_summary(self, features: list[str], *, periodic: bool, polling: bool) -> str:
        lead = "Application layer should initialize required integration points and then execute the requested behavior."
        if periodic:
            return f"{lead} Favor periodic, cadence-driven logic in app_main_loop while keeping hardware bindings explicit TODOs."
        if polling:
            return f"{lead} Favor polling-style loop behavior with explicit checks and conservative HAL integration points."
        return f"{lead} Keep hardware-specific integration behind TODO markers instead of inventing unsupported details."

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for value in values:
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            output.append(normalized)
        return output

    def _detect_driver_mentions(self, text: str) -> list[tuple[str, str]]:
        matches: list[tuple[str, str]] = []
        patterns = [
            r"\b([A-Z]{2,}[A-Z0-9_-]*\d[A-Z0-9_-]*)\b\s+(?:over|via|on)\s+(SPI|I2C|UART)\b",
            r"\b(SPI|I2C|UART)\s+(?:sensor|device|chip)?\s*([A-Z]{2,}[A-Z0-9_-]*\d[A-Z0-9_-]*)\b",
        ]
        for pattern in patterns:
            for left, right in re.findall(pattern, text, flags=re.IGNORECASE):
                if left.upper() in {"SPI", "I2C", "UART"}:
                    interface = left.upper()
                    chip = right.upper()
                else:
                    chip = left.upper()
                    interface = right.upper()
                if chip.startswith("STM32"):
                    continue
                pair = (chip, interface)
                if pair not in matches:
                    matches.append(pair)
        return matches
