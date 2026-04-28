from luxar.models.schemas import ProjectPlan
from luxar.prompts.gates import ANTI_RATIONALIZATION, VERIFICATION_GATE_APP, UART_DIAGNOSTIC_REQUIREMENT


APP_GENERATION_SYSTEM_PROMPT = f"""You are a senior embedded firmware engineer.

Generate concise, compilable C code for the application layer of an embedded project.
Follow these rules:
- Only generate application-layer code for `app_main.h` and `app_main.c`
- Do not modify CubeMX-generated files directly
- Keep the code compatible with STM32 HAL style projects
- Prefer simple polling/state-machine logic over complex abstractions
- Avoid malloc, free, and printf unless the requirement explicitly needs UART output
- Include Doxygen comments for exported functions
- Return exactly two fenced code blocks:
  1. ```c header
  2. ```c source

{VERIFICATION_GATE_APP}

{UART_DIAGNOSTIC_REQUIREMENT}

{ANTI_RATIONALIZATION}
"""


def build_app_generation_prompt(
    *,
    project_name: str,
    mcu: str,
    project_mode: str,
    project_plan: ProjectPlan,
    installed_drivers: list[str] | None = None,
) -> str:
    drivers = ", ".join(installed_drivers or []) or "none"
    features = "\n".join(f"- {item}" for item in project_plan.features) or "- none"
    peripheral_hints = "\n".join(f"- {item}" for item in project_plan.peripheral_hints) or "- none"
    config_actions = "\n".join(f"- {item}" for item in project_plan.cubemx_or_firmware_actions) or "- none"
    risks = "\n".join(f"- {item}" for item in project_plan.risk_notes) or "- none"
    return f"""Generate the application layer for the following embedded project.

[Project]
- Name: {project_name}
- MCU: {mcu}
- Project mode: {project_mode}
- Installed drivers: {drivers}

[Planned requirement summary]
{project_plan.requirement_summary}

[Features]
{features}

[App behavior summary]
{project_plan.app_behavior_summary}

[Document context summary]
{project_plan.document_context_summary or "No additional document context was provided."}

[Peripheral hints]
{peripheral_hints}

[Pending configuration actions]
{config_actions}

[Risk notes]
{risks}

[Output contract]
- `app_main.h` must declare `app_main_init(void)` and `app_main_loop(void)`
- `app_main.c` must implement those functions
- Use clear comments where hardware integration is still project-specific
- If the requirement mentions blinking or GPIO but pin configuration is unknown, keep the logic as a clear TODO inside the application layer
- If the requirement mentions UART output, prefer a weakly-coupled helper function or documented HAL integration points rather than hardcoding unsupported handles
- Keep the output minimal but compilable
"""
