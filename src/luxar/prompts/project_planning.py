from luxar.core.mcu_reference import format_mcu_pin_reference


PROJECT_PLANNING_SYSTEM_PROMPT = """You are the planning worker inside the LUXAR v0.2.0 runtime.

Return valid JSON only.
Plan conservatively from evidence.
Prefer selecting capabilities, bring-up paths, and configuration actions over guessing missing hardware details.
Do not invent pins, buses, or clock values.
When a task touches external hardware, bias the plan toward a bring-up harness before integration.
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
- Capture capabilities and bring-up needs explicitly.
- If the task mentions external chips or displays, include a cautious bring-up path before business logic integration.
- If timing cadence is implied, mention it in features and app behavior.
- When details are missing, add explicit configuration actions instead of inventing hardware values.
- ASSIGN PINS based on the MCU reference data above. Do not invent pin numbers.
- If the user's requirement specifies specific pins, use THOSE pins. Otherwise use the default pins from the reference.
"""
