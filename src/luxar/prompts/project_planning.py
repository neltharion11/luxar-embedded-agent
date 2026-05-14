from luxar.core.mcu_reference import format_mcu_pin_reference
from luxar.prompts.gates import ANTI_RATIONALIZATION, SELF_REVIEW_GATE


PROJECT_PLANNING_SYSTEM_PROMPT = f"""You are a senior STM32-focused embedded systems planner.

Turn a natural-language project request into a conservative structured execution plan.
Rules:
- Return valid JSON only.
- Do not guess unknown GPIO pins, UART instances, SPI buses, or clock values.
- Use the MCU hardware reference provided in the prompt for ALL pin/peripheral assignments.
- Every pin assignment in `peripheral_hints` and `cubemx_or_firmware_actions` MUST match the actual hardware reference.
- When hardware details are missing, add explicit configuration actions instead.
- Keep `needed_drivers` limited to concrete external devices or protocol-bound drivers.
- MCU-internal peripherals (GPIO, EXTI, ADC, DAC, TIM/PWM, UART/USART, SPI, I2C, CAN, USB, DMA, RTC, watchdogs, RCC/PWR/FLASH) MUST go in `internal_peripherals`, not `needed_drivers`.
- Board-level features (LED, RGB LED, button, buzzer, relay) MUST go in `board_features`, not `needed_drivers`.
- Use uppercase protocol names such as SPI, I2C, UART.

{SELF_REVIEW_GATE}

{ANTI_RATIONALIZATION}
"""


def build_project_planning_prompt(
    *,
    project_name: str,
    mcu: str,
    project_mode: str,
    requirement: str,
    document_context: str = "",
) -> str:
    doc_section = document_context.strip() or "No additional document context was provided."
    mcu_reference = format_mcu_pin_reference(mcu)
    return f"""Create a structured project plan for this embedded request.

[Project]
- Name: {project_name}
- MCU: {mcu}
- Project mode: {project_mode}

[Requirement]
{requirement}

[Document context]
{doc_section}

{mcu_reference}

[Required JSON schema]
{{
  "requirement_summary": "string",
  "features": ["string"],
  "needed_drivers": [
    {{
      "chip": "string",
      "interface": "SPI|I2C|UART",
      "vendor": "string",
      "device": "string",
      "confidence": 0.0,
      "rationale": "string"
    }}
  ],
  "internal_peripherals": [
    {{
      "interface": "GPIO|EXTI|ADC|DAC|TIM|PWM|UART|USART|SPI|I2C|CAN|USB|DMA|RTC|IWDG|WWDG|RCC|PWR|FLASH",
      "instance": "string",
      "mode": "string",
      "pins": {{"role": "pin"}},
      "clock_source": "string",
      "frequency": "string",
      "dma": ["string"],
      "irq": ["string"],
      "dependencies": ["string"],
      "owner": "firmware",
      "notes": "string"
    }}
  ],
  "external_devices": [
    {{
      "chip": "string",
      "interface": "SPI|I2C|UART",
      "vendor": "string",
      "device": "string",
      "confidence": 0.0,
      "rationale": "string"
    }}
  ],
  "board_features": [
    {{
      "interface": "BOARD",
      "instance": "LED|RGB_LED|BUTTON|BUZZER|RELAY|string",
      "mode": "string",
      "pins": {{"role": "pin"}},
      "dependencies": ["GPIO|TIM|EXTI|string"],
      "owner": "app",
      "notes": "string"
    }}
  ],
  "middleware_services": ["string"],
  "transport_bindings": [
    {{
      "device": "string",
      "driver": "string",
      "transport": "SPI|I2C|UART",
      "peripheral": "string",
      "pins": {{"role": "pin"}},
      "callbacks": ["string"],
      "notes": "string"
    }}
  ],
  "peripheral_hints": ["string"],
  "cubemx_or_firmware_actions": ["string"],
  "app_behavior_summary": "string",
  "document_context_summary": "string",
  "risk_notes": ["string"]
}}

[Planning guidance]
- If the request mentions LED blinking, capture periodic behavior and GPIO output needs.
- If the request mentions UART logging or printing, capture UART TX requirements.
- If the request mentions external sensors or chips over SPI/I2C/UART, add them to `needed_drivers` and `external_devices`.
- If the request only mentions a bus/peripheral instance such as TIM3, USART2, I2C1, SPI1, ADC1, or DMA, add it to `internal_peripherals` only.
- If timing cadence is implied (for example "once per second"), mention it in features and app behavior.
- When details are missing, add configuration TODO actions instead of inventing hardware values.
- ASSIGN PINS based on the MCU reference data above. Do not invent pin numbers.
- If the user's requirement specifies specific pins, use THOSE pins. Otherwise use the default pins from the reference.
"""
