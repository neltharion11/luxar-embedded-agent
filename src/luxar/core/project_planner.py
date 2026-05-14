from __future__ import annotations

import json
import re

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient, LLMClientError
from luxar.core.mcu_reference import get_mcu_pin_map
from luxar.models.schemas import (
    DriverBinding,
    DriverRequirement,
    EngineeringContext,
    PeripheralCapability,
    ProjectConfig,
    ProjectPlan,
)
from luxar.prompts.project_planning import (
    PROJECT_PLANNING_SYSTEM_PROMPT,
    build_project_planning_prompt,
)


INTERNAL_INTERFACES = {
    "ADC", "CAN", "DAC", "DMA", "EXTI", "FLASH", "GPIO", "I2C", "IWDG",
    "PWM", "RCC", "RTC", "SPI", "TIM", "TIMER", "UART", "USART", "USB", "WWDG",
}

INTERNAL_INSTANCE_RE = re.compile(
    r"\b(?:GPIO[A-K]?|EXTI\d*|DMA\d*|ADC\d*|DAC\d*|TIM\d*|TIMER\d*|PWM|"
    r"USART\d*|UART\d*|SPI\d*|I2C\d*|CAN\d*|USB|RTC|IWDG|WWDG|RCC|PWR|FLASH)\b",
    flags=re.IGNORECASE,
)

PIN_RE = re.compile(r"(?<![A-Z0-9_])P[A-K](?:1[0-5]|[0-9])(?![A-Z0-9_])", flags=re.IGNORECASE)
RGB_HINT_RE = re.compile(r"\b(rgb|rainbow)\b|彩虹|三色|彩色", flags=re.IGNORECASE)


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
            plan = plan.model_copy(update={"used_fallback": False, "raw_response": response.content, "engineering_context": engineering_context})
            return self.sanitize_plan(project=project, requirement=requirement, plan=plan)
        except (LLMClientError, ValueError, json.JSONDecodeError):
            return self.sanitize_plan(
                project=project,
                requirement=requirement,
                plan=self._fallback_plan(
                    project=project,
                    requirement=requirement,
                    document_context=document_context,
                    engineering_context=engineering_context,
                ),
            )

    def sanitize_plan(self, *, project: ProjectConfig, requirement: str, plan: ProjectPlan) -> ProjectPlan:
        """Normalize hardware intent so only external devices reach driver generation."""
        inferred_internal = self._infer_internal_peripherals(requirement, project.mcu)
        inferred_board = self._infer_board_features(requirement, project.mcu)

        external: list[DriverRequirement] = []
        internal = list(plan.internal_peripherals)
        board_features = list(plan.board_features)
        risks = list(plan.risk_notes)

        candidate_drivers = list(plan.needed_drivers) + list(plan.external_devices)
        for item in candidate_drivers:
            if self._looks_like_placeholder_device(item.chip):
                risks.append(
                    f"Removed placeholder-looking driver requirement '{item.chip}'. "
                    "Only concrete external device part numbers should enter driver generation."
                )
                continue
            if self._is_internal_driver_requirement(item):
                cap = self._driver_requirement_to_capability(item)
                if cap is not None:
                    internal.append(cap)
                continue
            external.append(item)

        internal.extend(self._prune_redundant_inferred_internal(existing=internal, inferred=inferred_internal))
        board_features.extend(inferred_board)
        internal.extend(self._infer_transport_peripherals_for_external_devices(external, internal, project.mcu, requirement))
        internal = self._filter_internal_capabilities(internal, board_features=board_features)
        internal = self._dedupe_capabilities(internal)
        board_features = self._dedupe_capabilities(board_features)
        external = self._dedupe_driver_requirements(external)
        peripheral_hints = self._augment_peripheral_hints(
            self._filter_misleading_default_hints(plan.peripheral_hints, requirement=requirement),
            internal_peripherals=internal,
            external_devices=external,
        )

        bindings = self._dedupe_bindings(
            list(plan.transport_bindings)
            + [self._binding_for_external_device(item, internal) for item in external]
        )

        return plan.model_copy(
            update={
                "needed_drivers": external,
                "external_devices": external,
                "internal_peripherals": internal,
                "board_features": board_features,
                "transport_bindings": bindings,
                "peripheral_hints": peripheral_hints,
                "risk_notes": self._dedupe(risks),
            }
        )

    def _fallback_plan(self, *, project: ProjectConfig, requirement: str, document_context: str = "", engineering_context: EngineeringContext | None = None) -> ProjectPlan:
        text = " ".join(requirement.strip().split())
        normalized_text = self._normalize_requirement_text(text)
        lowered = normalized_text.lower()
        features: list[str] = []
        peripheral_hints: list[str] = []
        actions: list[str] = []
        risks: list[str] = []
        needed_drivers: list[DriverRequirement] = []
        mcu_data = get_mcu_pin_map(project.mcu)

        blink = "blink" in lowered or ("led" in lowered and "oled" not in lowered)
        uart_log = any(token in lowered for token in ("uart", "serial", "print", "log", "hello"))
        periodic = any(token in lowered for token in ("every", "periodic", "per second", "1hz", "once per second"))
        polling = "poll" in lowered or "polling" in lowered

        led_pin = None
        uart_instance = None
        uart_pins = None
        if mcu_data:
            peri = mcu_data.get("peripherals", {})
            uart_info = peri.get("USART2", peri.get("USART1", {}))
            if uart_info:
                uart_instance = "USART2"
                uart_pins = uart_info.get("pins", {})

        if blink:
            features.append("Blink an LED from the application loop.")
            if mcu_data:
                led_pin = "PC13"
                peripheral_hints.append(f"GPIO output on PC13 (built-in Blue Pill LED, active-low) for the LED indicator.")
                actions.append(f"Configure PC13 as push-pull output, active-low (on Blue Pill the built-in LED is on PC13).")
            else:
                peripheral_hints.append("GPIO output required for an LED indicator.")
                if project.project_mode == "cubemx":
                    actions.append("Configure one GPIO pin as an output for the LED in CubeMX and document the label used by App code.")
                else:
                    actions.append("Wire one LED GPIO output into the firmware project and expose its HAL integration point to App code.")
                risks.append("LED pin is not specified; application code must keep GPIO integration as a TODO instead of guessing a pin.")

        if uart_log:
            features.append("Emit UART log output from the application layer.")
            if uart_instance and uart_pins:
                tx = uart_pins.get("TX", "PA2")
                rx = uart_pins.get("RX", "PA3")
                peripheral_hints.append(f"UART TX/RX on {uart_instance} ({tx}-TX, {rx}-RX, 115200 8N1).")
                actions.append(f"Enable {uart_instance} ({tx}-TX, {rx}-RX) at 115200 8N1 for debug output.")
            else:
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

        driver_mentions = self._detect_driver_mentions(normalized_text)
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
            chip_name = self._detect_chip_name(normalized_text)
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

    def _normalize_requirement_text(self, text: str) -> str:
        normalized = text
        replacements = {
            "I²C": "I2C",
            "I²c": "I2C",
            "i²c": "i2c",
            "IIC": "I2C",
            "iic": "i2c",
            "ＲＧＢ": "RGB",
            "ＯＬＥＤ": "OLED",
        }
        for old, new in replacements.items():
            normalized = normalized.replace(old, new)
        return normalized

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
            if token.startswith("STM32") or self._is_internal_token(token) or self._looks_like_placeholder_device(token):
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
                if self._is_internal_token(chip) or self._looks_like_placeholder_device(chip):
                    continue
                pair = (chip, interface)
                if pair not in matches:
                    matches.append(pair)
        return matches

    def _is_internal_token(self, token: str) -> bool:
        normalized = token.strip().upper().replace("-", "")
        if normalized in INTERNAL_INTERFACES or normalized in {"PWR"}:
            return True
        return INTERNAL_INSTANCE_RE.fullmatch(normalized) is not None

    def _looks_like_placeholder_device(self, chip: str) -> bool:
        return re.fullmatch(r"EMB[-_]\d+", chip.strip().upper()) is not None

    def _is_internal_driver_requirement(self, item: DriverRequirement) -> bool:
        interface = item.interface.strip().upper()
        chip = item.chip.strip().upper()
        device = item.device.strip().upper()
        if interface in {"GPIO", "PWM", "TIMER", "TIM", "ADC", "DAC", "DMA", "EXTI", "RCC", "PWR", "FLASH", "RTC", "IWDG", "WWDG", "USB", "CAN"}:
            return True
        if self._is_internal_token(chip) or (device and self._is_internal_token(device)):
            return True
        if chip in INTERNAL_INTERFACES:
            return True
        return False

    def _driver_requirement_to_capability(self, item: DriverRequirement) -> PeripheralCapability | None:
        token = item.chip.strip().upper() or item.interface.strip().upper()
        interface = item.interface.strip().upper()
        if interface == "PWM":
            interface = "TIM"
        if interface == "TIMER":
            interface = "TIM"
        if token in INTERNAL_INTERFACES:
            token = ""
        return PeripheralCapability(
            interface=interface,
            instance=token,
            mode=item.interface.strip().upper(),
            owner="firmware",
            notes=item.rationale,
        )

    def _infer_internal_peripherals(self, requirement: str, mcu: str) -> list[PeripheralCapability]:
        text = requirement or ""
        lowered = text.lower()
        mcu_data = get_mcu_pin_map(mcu) or {}
        caps: list[PeripheralCapability] = []
        mentioned_pins = [pin.upper() for pin in PIN_RE.findall(text)]
        tokens = {match.group(0).upper() for match in INTERNAL_INSTANCE_RE.finditer(text)}

        uart_instances = {
            token for token in tokens
            if token.startswith(("UART", "USART")) and token not in {"UART", "USART"}
        }
        if any(token in lowered for token in ("uart", "usart", "serial", "printf", "print", "log", "串口")) and not uart_instances:
            tokens.add("USART2")
        if (
            ("pwm" in lowered or RGB_HINT_RE.search(text))
            and not any(token.startswith(("TIM", "TIMER")) for token in tokens)
        ):
            inferred_timer = self._infer_timer_from_pwm_pins(mcu_data, mentioned_pins)
            tokens.add(inferred_timer or "TIM3")
        if any(token in lowered for token in ("adc", "analog", "采样", "模拟")) and not any(token.startswith("ADC") and token != "ADC" for token in tokens):
            tokens.add("ADC1")
        if any(token in lowered for token in ("can", "bxcan")) and not any(token.startswith("CAN") and token != "CAN" for token in tokens):
            tokens.add("CAN1")
        if "usb" in lowered and "USB" not in tokens:
            tokens.add("USB")
        if "rtc" in lowered and "RTC" not in tokens:
            tokens.add("RTC")

        for token in sorted(tokens):
            cap = self._capability_from_instance(token, mcu_data, mentioned_pins, lowered)
            if cap is not None:
                caps.append(cap)

        if mentioned_pins and any(token in lowered for token in ("gpio", "led", "button", "key", "按键", "蜂鸣器", "buzzer")):
            caps.append(
                PeripheralCapability(
                    interface="GPIO",
                    instance="GPIO",
                    mode="mixed",
                    pins={f"PIN{index + 1}": pin for index, pin in enumerate(mentioned_pins)},
                    owner="firmware",
                    notes="Explicit GPIO-capable pins mentioned in requirement.",
                )
            )
        return self._dedupe_capabilities(caps)

    def _infer_transport_peripherals_for_external_devices(
        self,
        external_devices: list[DriverRequirement],
        existing: list[PeripheralCapability],
        mcu: str,
        requirement: str,
    ) -> list[PeripheralCapability]:
        mcu_data = get_mcu_pin_map(mcu) or {}
        inferred: list[PeripheralCapability] = []
        for item in external_devices:
            interface = item.interface.strip().upper()
            if interface not in {"SPI", "I2C", "UART"}:
                continue
            if any(cap.interface.upper() == interface for cap in [*existing, *inferred]):
                continue
            default_instance = {"SPI": "SPI1", "I2C": "I2C1", "UART": "USART2"}[interface]
            cap = self._capability_from_instance(default_instance, mcu_data, [], requirement.lower())
            if cap is None:
                cap = PeripheralCapability(
                    interface=interface,
                    instance=default_instance,
                    mode="master" if interface in {"SPI", "I2C"} else "async",
                    owner="firmware",
                    notes=f"Inferred default {default_instance} transport for external device {item.chip}.",
                )
            else:
                cap = cap.model_copy(update={"notes": f"Inferred default {default_instance} transport for external device {item.chip}."})
            inferred.append(cap)
        return inferred

    def _augment_peripheral_hints(
        self,
        hints: list[str],
        *,
        internal_peripherals: list[PeripheralCapability],
        external_devices: list[DriverRequirement],
    ) -> list[str]:
        augmented = list(hints)
        for cap in internal_peripherals:
            name = cap.instance or cap.interface
            if not name:
                continue
            if cap.interface.upper() == "UART":
                augmented.append(f"UART peripheral {name} is required for serial/debug output.")
            elif cap.interface.upper() in {"SPI", "I2C", "CAN", "ADC", "DAC", "TIM", "DMA", "EXTI", "USB", "RTC"}:
                augmented.append(f"{cap.interface.upper()} peripheral {name} is required by the hardware plan.")
        for item in external_devices:
            augmented.append(f"{item.interface.upper()} transport is required to communicate with {item.chip}.")
        return self._dedupe(augmented)

    def _filter_misleading_default_hints(self, hints: list[str], *, requirement: str) -> list[str]:
        pins = PIN_RE.findall(requirement or "")
        if not pins or not ("led" in requirement.lower() or "灯" in requirement or RGB_HINT_RE.search(requirement or "")):
            return hints
        return [
            hint
            for hint in hints
            if "PC13" not in hint.upper() and "BUILT-IN" not in hint.upper() and "BLUE PILL" not in hint.upper()
        ]

    def _capability_from_instance(
        self,
        token: str,
        mcu_data: dict,
        mentioned_pins: list[str],
        lowered: str,
    ) -> PeripheralCapability | None:
        normalized = token.upper()
        if normalized in {"RCC", "PWR", "FLASH"}:
            return PeripheralCapability(interface=normalized, instance=normalized, mode="system", owner="firmware")
        if normalized in {"PWM", "TIM", "TIMER", "UART", "USART", "SPI", "I2C", "ADC", "CAN", "GPIO"}:
            return None
        if normalized.startswith("TIMER"):
            normalized = normalized.replace("TIMER", "TIM", 1)

        peripherals = mcu_data.get("peripherals", {})
        timers = mcu_data.get("timer_pwm_channels", {})
        adcs = mcu_data.get("adc_channels", {})

        if normalized.startswith(("USART", "UART")):
            instance = normalized.replace("UART", "USART", 1) if normalized.startswith("UART") else normalized
            info = peripherals.get(instance, {})
            return PeripheralCapability(
                interface="UART",
                instance=instance,
                mode="async",
                pins=dict(info.get("pins", {})),
                clock_source=info.get("bus", ""),
                frequency=self._extract_frequency_hint(lowered, default="115200 baud" if "115200" in lowered else ""),
                owner="firmware",
            )
        if normalized.startswith("SPI"):
            info = peripherals.get(normalized, {})
            return PeripheralCapability(
                interface="SPI",
                instance=normalized,
                mode="master",
                pins=dict(info.get("pins", {})),
                clock_source=info.get("bus", ""),
                owner="firmware",
            )
        if normalized.startswith("I2C"):
            info = peripherals.get(normalized, {})
            return PeripheralCapability(
                interface="I2C",
                instance=normalized,
                mode="master",
                pins=dict(info.get("pins", {})),
                clock_source=info.get("bus", ""),
                owner="firmware",
            )
        if normalized.startswith("TIM"):
            info = timers.get(normalized, {})
            pins = dict(info.get("channels", {}))
            if mentioned_pins:
                selected = {channel: pin for channel, pin in pins.items() if pin.upper() in mentioned_pins}
                pins = selected or pins
            return PeripheralCapability(
                interface="TIM",
                instance=normalized,
                mode="PWM" if "pwm" in lowered or "rainbow" in lowered or "rgb" in lowered else "timer",
                pins=pins,
                clock_source=info.get("bus", ""),
                frequency=self._extract_frequency_hint(lowered),
                owner="firmware",
            )
        if normalized.startswith("ADC"):
            info = adcs.get(normalized, {})
            pins = dict(info.get("channels", {}))
            if mentioned_pins:
                selected = {channel: pin for channel, pin in pins.items() if pin.upper() in mentioned_pins}
                pins = selected or pins
            return PeripheralCapability(
                interface="ADC",
                instance=normalized,
                mode="regular",
                pins=pins,
                clock_source=info.get("bus", ""),
                owner="firmware",
            )
        if normalized.startswith("DMA"):
            return PeripheralCapability(interface="DMA", instance=normalized, mode="controller", owner="firmware")
        if normalized.startswith("EXTI"):
            return PeripheralCapability(interface="EXTI", instance=normalized, mode="interrupt", owner="firmware")
        if normalized.startswith("CAN"):
            return PeripheralCapability(interface="CAN", instance=normalized, mode="normal", owner="firmware")
        if normalized in {"USB", "RTC", "IWDG", "WWDG"}:
            return PeripheralCapability(interface=normalized, instance=normalized, mode="enabled", owner="firmware")
        if normalized.startswith("GPIO"):
            return PeripheralCapability(interface="GPIO", instance=normalized, mode="gpio", owner="firmware")
        return None

    def _infer_timer_from_pwm_pins(self, mcu_data: dict, pins: list[str]) -> str:
        if not pins:
            return ""
        pin_set = set(pins)
        best_timer = ""
        best_score = 0
        for timer, info in mcu_data.get("timer_pwm_channels", {}).items():
            channels = info.get("channels", {})
            score = sum(1 for pin in channels.values() if pin.upper() in pin_set)
            if score > best_score:
                best_score = score
                best_timer = timer
        return best_timer

    def _infer_board_features(self, requirement: str, mcu: str) -> list[PeripheralCapability]:
        text = self._normalize_requirement_text(requirement or "")
        lowered = text.lower()
        pins = [pin.upper() for pin in PIN_RE.findall(text)]
        features: list[PeripheralCapability] = []
        if RGB_HINT_RE.search(text) or "呼吸" in text or ("red" in lowered and "green" in lowered and "blue" in lowered):
            pin_roles = {}
            for role, pin in zip(("R", "G", "B"), pins[:3]):
                pin_roles[role] = pin
            features.append(
                PeripheralCapability(
                    interface="BOARD",
                    instance="RGB_LED",
                    mode="pwm_output",
                    pins=pin_roles,
                    dependencies=["TIM", "GPIO"],
                    owner="app",
                    notes="Board-level RGB LED feature; timer/GPIO setup stays in firmware scaffold.",
                )
            )
        elif "led" in lowered or "灯" in lowered:
            pin = pins[0] if pins else ("PC13" if (get_mcu_pin_map(mcu) or {}).get("package") == "LQFP48" else "")
            features.append(
                PeripheralCapability(
                    interface="BOARD",
                    instance="LED",
                    mode="gpio_output",
                    pins={"LED": pin} if pin else {},
                    dependencies=["GPIO"],
                    owner="app",
                )
            )
        if any(token in lowered for token in ("button", "key", "按键")):
            features.append(
                PeripheralCapability(
                    interface="BOARD",
                    instance="BUTTON",
                    mode="gpio_input",
                    pins={"BUTTON": pins[0]} if pins else {},
                    dependencies=["GPIO", "EXTI"],
                    owner="app",
                )
            )
        if any(token in lowered for token in ("buzzer", "蜂鸣器")):
            features.append(
                PeripheralCapability(
                    interface="BOARD",
                    instance="BUZZER",
                    mode="gpio_or_pwm_output",
                    pins={"BUZZER": pins[0]} if pins else {},
                    dependencies=["GPIO"],
                    owner="app",
                )
            )
        return self._dedupe_capabilities(features)

    def _binding_for_external_device(
        self,
        item: DriverRequirement,
        internal_peripherals: list[PeripheralCapability],
    ) -> DriverBinding:
        interface = item.interface.strip().upper()
        peripheral = next(
            (
                cap.instance
                for cap in internal_peripherals
                if cap.interface.upper() == interface or (interface == "UART" and cap.interface.upper() == "USART")
            ),
            "",
        )
        callbacks = ["delay_ms"]
        if interface in {"SPI", "I2C", "UART"}:
            callbacks.extend([f"{interface.lower()}_transfer" if interface == "SPI" else f"{interface.lower()}_txrx"])
        driver_name = item.chip.strip().lower() or item.device.strip().lower()
        device_name = item.device.strip() or item.chip.strip().lower()
        return DriverBinding(
            device=device_name,
            driver=driver_name,
            transport=interface,
            peripheral=peripheral,
            callbacks=callbacks,
            notes="External driver must receive transport functions/handles through this binding.",
        )

    def _extract_frequency_hint(self, lowered: str, default: str = "") -> str:
        match = re.search(r"(?:~|about\s+)?(\d+(?:\.\d+)?)\s*(khz|mhz|hz|baud)", lowered, flags=re.IGNORECASE)
        if not match:
            return default
        return f"{match.group(1)} {match.group(2).lower()}"

    def _dedupe_driver_requirements(self, values: list[DriverRequirement]) -> list[DriverRequirement]:
        output: list[DriverRequirement] = []
        by_key: dict[tuple[str, str], DriverRequirement] = {}
        for item in values:
            chip = item.chip.strip().upper()
            interface = item.interface.strip().upper()
            if not chip:
                continue
            key = (chip, interface)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = item
                output.append(item)
                continue
            merged = self._merge_driver_requirements(existing, item)
            by_key[key] = merged
            output[output.index(existing)] = merged
        return output

    def _merge_driver_requirements(self, left: DriverRequirement, right: DriverRequirement) -> DriverRequirement:
        left_device = left.device.strip()
        right_device = right.device.strip()
        left_vendor = left.vendor.strip()
        right_vendor = right.vendor.strip()
        device = left_device if len(left_device) >= len(right_device) else right_device
        vendor = left_vendor if len(left_vendor) >= len(right_vendor) else right_vendor
        confidence = max(left.confidence, right.confidence)
        rationale = left.rationale.strip()
        right_rationale = right.rationale.strip()
        if right_rationale and right_rationale not in rationale:
            rationale = " | ".join(part for part in [rationale, right_rationale] if part)
        return left.model_copy(
            update={
                "device": device,
                "vendor": vendor,
                "confidence": confidence,
                "rationale": rationale,
            }
        )

    def _prune_redundant_inferred_internal(
        self,
        *,
        existing: list[PeripheralCapability],
        inferred: list[PeripheralCapability],
    ) -> list[PeripheralCapability]:
        existing_timer_instances = {
            cap.instance.strip().upper()
            for cap in existing
            if cap.interface.strip().upper() in {"TIM", "PWM"} and cap.instance.strip()
        }
        if not existing_timer_instances:
            return inferred
        pruned: list[PeripheralCapability] = []
        for cap in inferred:
            interface = cap.interface.strip().upper()
            instance = cap.instance.strip().upper()
            if interface in {"TIM", "PWM"} and instance and instance not in existing_timer_instances:
                continue
            pruned.append(cap)
        return pruned

    def _filter_internal_capabilities(
        self,
        values: list[PeripheralCapability],
        *,
        board_features: list[PeripheralCapability],
    ) -> list[PeripheralCapability]:
        board_pins = {
            pin.upper()
            for feature in board_features
            for pin in feature.pins.values()
            if pin
        }
        filtered: list[PeripheralCapability] = []
        for item in values:
            interface = item.interface.strip().upper()
            instance = item.instance.strip()
            if interface == "GPIO":
                normalized_instance = instance.upper()
                if normalized_instance == "GPIO":
                    continue
                if "LED" in normalized_instance and any(pin.upper() in board_pins for pin in item.pins.values()):
                    continue
                if "(" in instance or ")" in instance:
                    continue
            filtered.append(item)
        return filtered

    def _dedupe_capabilities(self, values: list[PeripheralCapability]) -> list[PeripheralCapability]:
        output: list[PeripheralCapability] = []
        by_key: dict[tuple[str, str], PeripheralCapability] = {}
        for item in values:
            key = self._capability_merge_key(item)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = item
                output.append(item)
                continue
            merged = self._merge_capabilities(existing, item)
            by_key[key] = merged
            output[output.index(existing)] = merged
        return output

    def _dedupe_bindings(self, values: list[DriverBinding]) -> list[DriverBinding]:
        seen: set[tuple[str, str, str]] = set()
        output: list[DriverBinding] = []
        for item in values:
            binding_name = item.driver.strip().lower() or item.device.strip().lower()
            key = (binding_name, item.transport.strip().upper(), item.peripheral.strip().upper())
            if not key[0] or key in seen:
                continue
            seen.add(key)
            output.append(item)
        return output

    def _capability_merge_key(self, item: PeripheralCapability) -> tuple[str, str]:
        interface = item.interface.strip().upper()
        instance = item.instance.strip().upper()
        if interface == "BOARD":
            return (interface, instance)
        if interface == "PWM" and instance.startswith("TIM"):
            interface = "TIM"
        return (interface, instance)

    def _merge_capabilities(self, left: PeripheralCapability, right: PeripheralCapability) -> PeripheralCapability:
        pins = dict(left.pins)
        pins.update({key: value for key, value in right.pins.items() if value})
        dependencies = self._dedupe([*left.dependencies, *right.dependencies])
        dma = self._dedupe([*left.dma, *right.dma])
        irq = self._dedupe([*left.irq, *right.irq])
        notes = " | ".join(part for part in [left.notes.strip(), right.notes.strip()] if part)
        mode = right.mode if len(right.pins) > len(left.pins) and right.mode else left.mode or right.mode
        clock_source = left.clock_source or right.clock_source
        frequency = left.frequency or right.frequency
        owner = left.owner if left.owner != "firmware" else right.owner or left.owner
        return left.model_copy(
            update={
                "mode": mode,
                "pins": pins,
                "clock_source": clock_source,
                "frequency": frequency,
                "dma": dma,
                "irq": irq,
                "dependencies": dependencies,
                "owner": owner,
                "notes": notes,
            }
        )
