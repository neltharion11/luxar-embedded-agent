from luxar.core.mcu_reference import format_app_generation_mcu_ref
from luxar.models.schemas import ProjectPlan


APP_GENERATION_SYSTEM_PROMPT = """You are the LUXAR v0.2.0 application-generation worker.

Generate concise, compilable application-layer C code.
Treat bring-up and transport ownership as upstream concerns; application code should integrate behavior only after the selected harness path is satisfied.
Do not invent hardware bindings or hidden initialization flows.
Return exactly two fenced code blocks:
1. ```c header
2. ```c source
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
    internal = "\n".join(
        f"- {item.interface} {item.instance} mode={item.mode} pins={item.pins}"
        for item in project_plan.internal_peripherals
    ) or "- none"
    board = "\n".join(
        f"- {item.instance} mode={item.mode} pins={item.pins}"
        for item in project_plan.board_features
    ) or "- none"
    bindings = "\n".join(
        f"- {item.device}: {item.driver} over {item.transport} via {item.peripheral}"
        for item in project_plan.transport_bindings
    ) or "- none"
    mcu_pin_ref = format_app_generation_mcu_ref(mcu)
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

[Internal peripherals owned by firmware scaffold]
{internal}

[Board features]
{board}

[External driver bindings]
{bindings}

[Pending configuration actions]
{config_actions}

[Risk notes]
{risks}

{mcu_pin_ref}

[PIN ASSIGNMENT RULES — VIOLATING THESE IS A HARD ERROR]
1. Use ONLY the pin/peripheral assignments listed in the MCU PIN REFERENCE above.
2. NEVER fabricate pin numbers. If a peripheral isn't listed, don't use it.
3. PC13 is the built-in LED (active-low) on STM32F103C8T6 Blue Pill.
4. USART2 defaults to PA2(TX), PA3(RX) @ 115200 8N1 for debug output.
5. PA13(SWDIO) and PA14(SWCLK) are RESERVED for debug — never use as GPIO.
6. If the user specified pins in their requirement, use those pins.

[Output contract]
- `app_main.h` must declare `app_main_init(void)` and `app_main_loop(void)`
- `app_main.c` must implement those functions
- Firmware-mode app code may include `luxar_hardware.h` and call its glue functions; do not recreate peripheral initialization.
- Use clear comments where hardware integration is still project-specific
- If the requirement mentions blinking or GPIO but pin configuration is unknown, keep the logic as a clear TODO inside the application layer
- If the requirement mentions UART output, prefer a weakly-coupled helper function or documented integration points rather than hardcoding unsupported handles
- Do not add generated driver files or new peripheral init code under App/Drivers.
- Keep the output minimal but compilable
- If the task implies hardware validation that has not happened yet, keep the app side conservative and assume the harness will validate runtime behavior separately.
"""
