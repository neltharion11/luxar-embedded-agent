from luxar.core.mcu_reference import format_app_generation_mcu_ref
from luxar.models.schemas import ProjectPlan
from luxar.prompts.gates import ANTI_RATIONALIZATION, VERIFICATION_GATE_APP, UART_DIAGNOSTIC_REQUIREMENT


APP_GENERATION_SYSTEM_PROMPT = f"""You are a senior embedded firmware engineer.

Generate concise, compilable C code for the application layer of an embedded project.
Follow these rules:
- Only generate application-layer code for `app_main.h` and `app_main.c`
- Do not modify CubeMX-generated files directly
- Do not perform low-level peripheral initialization in App code for firmware-mode projects. Use `luxar_hardware.h` scaffold/glue functions or existing HAL handles documented in the plan.
- Treat `internal_peripherals` as already owned by the firmware scaffold. App code should implement behavior/state machines and call integration helpers such as `luxar_uart_write`, `luxar_rgb_pwm_set`, `luxar_delay_ms`, `luxar_spi_transfer`, or `luxar_i2c_txrx` when applicable.
- PINS AND PERIPHERALS: Use ONLY the MCU pin reference provided in the prompt. Do NOT hallucinate pin numbers, GPIO ports, or peripheral instances. If the prompt says USART2 is on PA2/PA3, use PA2/PA3. If the prompt says the LED is on PC13, use PC13.
- USE THE STM32 HAL LIBRARY (stm32f1xx_hal.h) for ALL peripheral access:
  * GPIO: HAL_GPIO_WritePin, HAL_GPIO_TogglePin, HAL_GPIO_Init
  * UART: HAL_UART_Transmit (never use bare-metal USART register writes)
  * SysTick: use HAL_Delay for timing, HAL_GetTick for elapsed time
  * Clock: use HAL_RCC_OscConfig / HAL_RCC_ClockConfig
  * Do NOT write to registers directly (no USART2->SR, GPIOA->BSRR, etc.)
  * Do NOT include stm32f10x.h (use stm32f1xx_hal.h instead)
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
- If the requirement mentions UART output, prefer a weakly-coupled helper function or documented HAL integration points rather than hardcoding unsupported handles
- Do not add generated driver files or new peripheral init code under App/Drivers.
- Keep the output minimal but compilable
"""
