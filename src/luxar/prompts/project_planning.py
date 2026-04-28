from luxar.prompts.gates import ANTI_RATIONALIZATION, SELF_REVIEW_GATE


PROJECT_PLANNING_SYSTEM_PROMPT = f"""You are a senior STM32-focused embedded systems planner.

Turn a natural-language project request into a conservative structured execution plan.
Rules:
- Return valid JSON only.
- Do not guess unknown GPIO pins, UART instances, SPI buses, or clock values.
- When hardware details are missing, add explicit configuration actions instead.
- Keep `needed_drivers` limited to concrete external devices or protocol-bound drivers.
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
    return f"""Create a structured project plan for this embedded request.

[Project]
- Name: {project_name}
- MCU: {mcu}
- Project mode: {project_mode}

[Requirement]
{requirement}

[Document context]
{doc_section}

[Required JSON schema]
{{
  "requirement_summary": "string",
  "features": ["string"],
  "needed_drivers": [
    {{
      "chip": "string",
      "interface": "SPI|I2C|UART|GPIO|PWM|ADC|TIMER",
      "vendor": "string",
      "device": "string",
      "confidence": 0.0,
      "rationale": "string"
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
- If the request mentions external sensors or chips over SPI/I2C/UART, add them to `needed_drivers`.
- If timing cadence is implied (for example "once per second"), mention it in features and app behavior.
- When details are missing, add configuration TODO actions instead of inventing hardware values.
"""
