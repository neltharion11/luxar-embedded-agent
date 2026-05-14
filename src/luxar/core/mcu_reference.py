from __future__ import annotations

from dataclasses import field
from typing import ClassVar


def _format_pin_map(pins: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in pins.items())


MCU_PIN_MAP: dict[str, dict] = {
    "STM32F103C8T6": {
        "package": "LQFP48",
        "core": "Cortex-M3",
        "max_sysclk_mhz": 72,
        "flash_kb": 64,
        "ram_kb": 20,
        "gpio_ports": ["A", "B", "C"],
        "swd_pins": {"SWDIO": "PA13", "SWCLK": "PA14"},
        "notes": [
            "PA13(SWDIO) and PA14(SWCLK) are reserved for debug. Avoid using them as GPIO.",
            "PB2 is BOOT1 — not available as GPIO in normal operation.",
            "PB3(TDO), PB4(JNTRST) default to JTAG. Use as GPIO only after disabling JTAG.",
            "PC13 is the built-in LED on Blue Pill boards (active-low).",
            "PC14-OSC32_IN and PC15-OSC32_OUT are for LSE crystal. Available as GPIO if LSE not used.",
        ],
        "peripherals": {
            "USART1": {"pins": {"TX": "PA9", "RX": "PA10"}, "bus": "APB2", "remap_pins": {"TX": "PB6", "RX": "PB7"}},
            "USART2": {"pins": {"TX": "PA2", "RX": "PA3"}, "bus": "APB1", "remap_pins": None},
            "USART3": {"pins": {"TX": "PB10", "RX": "PB11"}, "bus": "APB1", "remap_pins": None},
            "I2C1":  {"pins": {"SCL": "PB6", "SDA": "PB7"}, "bus": "APB1", "remap_pins": {"SCL": "PB8", "SDA": "PB9"}},
            "I2C2":  {"pins": {"SCL": "PB10", "SDA": "PB11"}, "bus": "APB1", "remap_pins": None},
            "SPI1":  {"pins": {"SCK": "PA5", "MISO": "PA6", "MOSI": "PA7", "NSS": "PA4"}, "bus": "APB2", "remap_pins": {"SCK": "PB3", "MISO": "PB4", "MOSI": "PB5", "NSS": "PA15"}},
            "SPI2":  {"pins": {"SCK": "PB13", "MISO": "PB14", "MOSI": "PB15", "NSS": "PB12"}, "bus": "APB1", "remap_pins": None},
        },
        "timer_pwm_channels": {
            "TIM1":  {"bus": "APB2", "channels": {"CH1": "PA8", "CH2": "PA9", "CH3": "PA10", "CH4": "PA11"}},
            "TIM2":  {"bus": "APB1", "channels": {"CH1": "PA0", "CH2": "PA1", "CH3": "PA2", "CH4": "PA3"}},
            "TIM3":  {"bus": "APB1", "channels": {"CH1": "PA6", "CH2": "PA7", "CH3": "PB0", "CH4": "PB1"}},
            "TIM4":  {"bus": "APB1", "channels": {"CH1": "PB6", "CH2": "PB7", "CH3": "PB8", "CH4": "PB9"}},
        },
        "adc_channels": {
            "ADC1": {"bus": "APB2", "channels": {"IN0": "PA0", "IN1": "PA1", "IN2": "PA2", "IN3": "PA3", "IN4": "PA4", "IN5": "PA5", "IN6": "PA6", "IN7": "PA7", "IN8": "PB0", "IN9": "PB1"}},
        },
    },
}


MCU_FAMILY_CLOCKS: dict[str, dict] = {
    "F1": {
        "hsi_hz": 8_000_000,
        "hse_typical_hz": 8_000_000,
        "max_pll_out_hz": 72_000_000,
        "apb1_max_hz": 36_000_000,
        "apb2_max_hz": 72_000_000,
        "note": "APB1 max is 36 MHz. APB1 timer clocks are 2x APB1 when prescaler != 1.",
    },
}


def get_mcu_pin_map(mcu_name: str) -> dict | None:
    normalized = mcu_name.strip().upper().replace(" ", "").replace("_", "")
    return MCU_PIN_MAP.get(normalized)


def get_family_clock_info(mcu_name: str) -> dict | None:
    normalized = mcu_name.strip().upper().replace(" ", "").replace("_", "")
    if normalized.startswith("STM32F1"):
        return MCU_FAMILY_CLOCKS.get("F1")
    for key in MCU_FAMILY_CLOCKS:
        if normalized.startswith(f"STM32{key}"):
            return MCU_FAMILY_CLOCKS[key]
    return None


def format_mcu_pin_reference(mcu_name: str) -> str:
    data = get_mcu_pin_map(mcu_name)
    if not data:
        return f"[WARNING] No pin/peripheral reference data for {mcu_name}. Do NOT invent pin assignments."
    lines = [
        f"## {mcu_name} Hardware Reference ({data['package']} package)",
        f"- Core: {data['core']}, Max SYSCLK: {data['max_sysclk_mhz']} MHz",
        f"- Flash: {data['flash_kb']} KB, RAM: {data['ram_kb']} KB",
        f"- GPIO Ports available: {', '.join(data['gpio_ports'])}",
        "",
        "### Reserved / Special Pins",
        f"- SWD Debug: {data['swd_pins']['SWDIO']} (SWDIO), {data['swd_pins']['SWCLK']} (SWCLK) — DO NOT use as GPIO",
    ]
    for note in data.get("notes", []):
        lines.append(f"- {note}")
    lines.append("")
    lines.append("### Peripheral Instances (Default Pins)")
    for name, info in data.get("peripherals", {}).items():
        pin_desc = _format_pin_map(info['pins'])
        lines.append(f"- **{name}** ({info['bus']}): {pin_desc}")
    lines.append("")
    lines.append("### Timer PWM Channels")
    for name, info in data.get("timer_pwm_channels", {}).items():
        channels = ", ".join(f"{ch}={pin}" for ch, pin in info['channels'].items())
        lines.append(f"- **{name}** ({info['bus']}): {channels}")
    lines.append("")
    lines.append("### Pin Assignment Rules")
    lines.append("- Assign pins based on the ACTUAL peripheral mapping above. Do not guess.")
    lines.append("- USART2 is the default debug UART on Blue Pill (PA2-TX, PA3-RX).")
    lines.append("- On Blue Pill / STM32F103C8T6, the built-in LED is PC13 (active-low).")
    lines.append("- For RGB LEDs, use 3 GPIO pins on the same or adjacent ports for efficiency.")
    lines.append("- When using software PWM or timer PWM, prefer pins that support TIMx_CHy hardware PWM.")
    lines.append("- If the user specifies specific pins in their requirement, use THOSE pins over defaults.")
    return "\n".join(lines)


def format_app_generation_mcu_ref(mcu_name: str) -> str:
    data = get_mcu_pin_map(mcu_name)
    if not data:
        return ""
    lines = [
        f"[MCU PIN REFERENCE: {mcu_name}]",
        "You MUST use the following real hardware pin assignments. Do NOT hallucinate pins.",
        f"- SWD (reserved): {data['swd_pins']['SWDIO']}, {data['swd_pins']['SWCLK']}",
    ]
    for name, info in data.get("peripherals", {}).items():
        pin_desc = _format_pin_map(info['pins'])
        lines.append(f"- {name} ({info['bus']}): {pin_desc}")
    for name, info in data.get("timer_pwm_channels", {}).items():
        chs = ", ".join(f"{ch}={pin}" for ch, pin in info['channels'].items())
        lines.append(f"- {name} PWM channels: {chs}")
    if "adc_channels" in data:
        for name, info in data["adc_channels"].items():
            chs = ", ".join(f"{ch}={pin}" for ch, pin in info['channels'].items())[:200]
            lines.append(f"- {name} ADC channels: {chs}...")
    lines.append("- PC13 = built-in LED (active-low on Blue Pill)")
    lines.append("- NEVER use PA13 or PA14 as GPIO (SWD debug pins)")
    lines.append("- If user specified pins in their requirement, use those pins; otherwise use defaults above.")
    return "\n".join(lines)
