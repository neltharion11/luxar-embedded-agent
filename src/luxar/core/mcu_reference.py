
# ── Lightweight peripheral existence per MCU family ──
import re
# Key: family prefix (matches _DEFINE_FAMILY_PREFIX keys). Value: lists of instance names.
MCU_FAMILY_PERIPHERALS: dict[str, dict[str, list[str]]] = {
    "STM32F0": {
        "tim":   ["TIM1", "TIM2", "TIM3", "TIM6", "TIM7", "TIM14", "TIM15", "TIM16", "TIM17"],
        "usart": ["USART1", "USART2"],
        "i2c":   ["I2C1", "I2C2"],
        "spi":   ["SPI1", "SPI2"],
        "adc":   ["ADC1"],
        "dac":   [],
        "can":   [],
        "sdio":  [],
        "usb":   [],
    },
    "STM32F1": {
        "tim":   ["TIM1", "TIM2", "TIM3", "TIM4"],
        "usart": ["USART1", "USART2", "USART3"],
        "i2c":   ["I2C1", "I2C2"],
        "spi":   ["SPI1", "SPI2"],
        "adc":   ["ADC1", "ADC2"],
        "dac":   [],
        "can":   ["CAN1"],
        "sdio":  [],
        "usb":   ["USB"],
    },
    "STM32F2": {
        "tim":   ["TIM1", "TIM2", "TIM3", "TIM4", "TIM5", "TIM6", "TIM7", "TIM8", "TIM9", "TIM10", "TIM11", "TIM12", "TIM13", "TIM14"],
        "usart": ["USART1", "USART2", "USART3", "UART4", "UART5", "USART6"],
        "i2c":   ["I2C1", "I2C2", "I2C3"],
        "spi":   ["SPI1", "SPI2", "SPI3"],
        "adc":   ["ADC1", "ADC2", "ADC3"],
        "dac":   ["DAC1"],
        "can":   ["CAN1", "CAN2"],
        "sdio":  ["SDIO"],
        "usb":   ["USB_OTG_FS", "USB_OTG_HS"],
    },
    "STM32F3": {
        "tim":   ["TIM1", "TIM2", "TIM3", "TIM4", "TIM6", "TIM7", "TIM8", "TIM15", "TIM16", "TIM17"],
        "usart": ["USART1", "USART2", "USART3", "UART4"],
        "i2c":   ["I2C1", "I2C2"],
        "spi":   ["SPI1", "SPI2", "SPI3"],
        "adc":   ["ADC1", "ADC2", "ADC3", "ADC4"],
        "dac":   ["DAC1", "DAC2"],
        "can":   ["CAN1"],
        "sdio":  [],
        "usb":   ["USB"],
    },
    "STM32F4": {
        "tim":   ["TIM1", "TIM2", "TIM3", "TIM4", "TIM5", "TIM6", "TIM7", "TIM8", "TIM9", "TIM10", "TIM11", "TIM12", "TIM13", "TIM14"],
        "usart": ["USART1", "USART2", "USART3", "UART4", "UART5", "USART6"],
        "i2c":   ["I2C1", "I2C2", "I2C3"],
        "spi":   ["SPI1", "SPI2", "SPI3"],
        "adc":   ["ADC1", "ADC2", "ADC3"],
        "dac":   ["DAC1", "DAC2"],
        "can":   ["CAN1", "CAN2"],
        "sdio":  ["SDIO"],
        "usb":   ["USB_OTG_FS", "USB_OTG_HS"],
    },
    "STM32F7": {
        "tim":   ["TIM1", "TIM2", "TIM3", "TIM4", "TIM5", "TIM6", "TIM7", "TIM8", "TIM9", "TIM10", "TIM11", "TIM12", "TIM13", "TIM14"],
        "usart": ["USART1", "USART2", "USART3", "UART4", "UART5", "USART6", "USART7", "UART8"],
        "i2c":   ["I2C1", "I2C2", "I2C3", "I2C4"],
        "spi":   ["SPI1", "SPI2", "SPI3", "SPI4", "SPI5", "SPI6"],
        "adc":   ["ADC1", "ADC2", "ADC3"],
        "dac":   ["DAC1", "DAC2"],
        "can":   ["CAN1", "CAN2"],
        "sdio":  ["SDIO"],
        "usb":   ["USB_OTG_FS", "USB_OTG_HS"],
    },
    "STM32G0": {
        "tim":   ["TIM1", "TIM2", "TIM3", "TIM6", "TIM7", "TIM14", "TIM15", "TIM16", "TIM17"],
        "usart": ["USART1", "USART2", "USART3", "USART4", "USART5", "USART6"],
        "i2c":   ["I2C1", "I2C2", "I2C3"],
        "spi":   ["SPI1", "SPI2", "SPI3"],
        "adc":   ["ADC1"],
        "dac":   ["DAC1"],
        "can":   ["CAN1", "CAN2"],
        "sdio":  [],
        "usb":   ["USB"],
    },
    "STM32G4": {
        "tim":   ["TIM1", "TIM2", "TIM3", "TIM4", "TIM5", "TIM6", "TIM7", "TIM8", "TIM15", "TIM16", "TIM17"],
        "usart": ["USART1", "USART2", "USART3", "UART4", "UART5"],
        "i2c":   ["I2C1", "I2C2", "I2C3", "I2C4"],
        "spi":   ["SPI1", "SPI2", "SPI3", "SPI4"],
        "adc":   ["ADC1", "ADC2", "ADC3", "ADC4", "ADC5"],
        "dac":   ["DAC1", "DAC2", "DAC3", "DAC4"],
        "can":   ["CAN1", "CAN2", "CAN3"],
        "sdio":  [],
        "usb":   ["USB"],
    },
    "STM32H7": {
        "tim":   ["TIM1", "TIM2", "TIM3", "TIM4", "TIM5", "TIM6", "TIM7", "TIM8", "TIM12", "TIM13", "TIM14", "TIM15", "TIM16", "TIM17"],
        "usart": ["USART1", "USART2", "USART3", "UART4", "UART5", "USART6", "UART7", "UART8"],
        "i2c":   ["I2C1", "I2C2", "I2C3", "I2C4"],
        "spi":   ["SPI1", "SPI2", "SPI3", "SPI4", "SPI5", "SPI6"],
        "adc":   ["ADC1", "ADC2", "ADC3"],
        "dac":   ["DAC1", "DAC2"],
        "can":   ["CAN1", "CAN2"],
        "sdio":  ["SDIO"],
        "usb":   ["USB_OTG_FS", "USB_OTG_HS"],
    },
    "STM32L0": {
        "tim":   ["TIM2", "TIM3", "TIM6", "TIM7", "TIM21", "TIM22"],
        "usart": ["USART1", "USART2"],
        "i2c":   ["I2C1", "I2C2", "I2C3"],
        "spi":   ["SPI1", "SPI2"],
        "adc":   ["ADC1"],
        "dac":   ["DAC1"],
        "can":   [],
        "sdio":  [],
        "usb":   ["USB"],
    },
    "STM32L1": {
        "tim":   ["TIM2", "TIM3", "TIM4", "TIM5", "TIM6", "TIM7", "TIM9", "TIM10", "TIM11"],
        "usart": ["USART1", "USART2"],
        "i2c":   ["I2C1", "I2C2"],
        "spi":   ["SPI1", "SPI2"],
        "adc":   ["ADC1"],
        "dac":   ["DAC1", "DAC2"],
        "can":   [],
        "sdio":  [],
        "usb":   ["USB"],
    },
    "STM32L4": {
        "tim":   ["TIM1", "TIM2", "TIM3", "TIM4", "TIM5", "TIM6", "TIM7", "TIM8", "TIM15", "TIM16", "TIM17"],
        "usart": ["USART1", "USART2", "USART3", "UART4", "UART5"],
        "i2c":   ["I2C1", "I2C2", "I2C3", "I2C4"],
        "spi":   ["SPI1", "SPI2", "SPI3"],
        "adc":   ["ADC1", "ADC2", "ADC3"],
        "dac":   ["DAC1", "DAC2"],
        "can":   ["CAN1", "CAN2"],
        "sdio":  ["SDIO"],
        "usb":   ["USB_OTG_FS"],
    },
    "STM32WB": {
        "tim":   ["TIM1", "TIM2", "TIM16", "TIM17"],
        "usart": ["USART1"],
        "i2c":   ["I2C1", "I2C3"],
        "spi":   ["SPI1", "SPI2"],
        "adc":   ["ADC1"],
        "dac":   [],
        "can":   [],
        "sdio":  [],
        "usb":   ["USB"],
    },
}

# ── Updated clock data for all families ──
MCU_FAMILY_CLOCKS: dict[str, dict] = {
    "F1": {
        "hsi_hz": 8_000_000,
        "hse_typical_hz": 8_000_000,
        "max_pll_out_hz": 72_000_000,
        "apb1_max_hz": 36_000_000,
        "apb2_max_hz": 72_000_000,
        "note": "APB1 max 36 MHz. APB1 timer clocks 2x when prescaler != 1.",
    },
    "F4": {
        "hsi_hz": 16_000_000,
        "hse_typical_hz": 8_000_000,
        "max_pll_out_hz": 168_000_000,
        "apb1_max_hz": 42_000_000,
        "apb2_max_hz": 84_000_000,
        "note": "Max SYSCLK 168 MHz. APB1 max 42 MHz, APB2 max 84 MHz.",
    },
    "F7": {
        "hsi_hz": 16_000_000,
        "hse_typical_hz": 25_000_000,
        "max_pll_out_hz": 216_000_000,
        "apb1_max_hz": 54_000_000,
        "apb2_max_hz": 108_000_000,
        "note": "Max SYSCLK 216 MHz. APB1 max 54 MHz, APB2 max 108 MHz.",
    },
    "H7": {
        "hsi_hz": 64_000_000,
        "hse_typical_hz": 25_000_000,
        "max_pll_out_hz": 480_000_000,
        "apb1_max_hz": 120_000_000,
        "apb2_max_hz": 120_000_000,
        "note": "Max SYSCLK 480 MHz. Dual-core (M7+M4) on some models.",
    },
    "G0": {
        "hsi_hz": 16_000_000,
        "hse_typical_hz": 8_000_000,
        "max_pll_out_hz": 64_000_000,
        "apb_max_hz": 64_000_000,
        "note": "Max SYSCLK 64 MHz. Single APB bus.",
    },
    "G4": {
        "hsi_hz": 16_000_000,
        "hse_typical_hz": 8_000_000,
        "max_pll_out_hz": 170_000_000,
        "apb1_max_hz": 85_000_000,
        "apb2_max_hz": 85_000_000,
        "note": "Max SYSCLK 170 MHz.",
    },
    "L4": {
        "hsi_hz": 16_000_000,
        "hse_typical_hz": 8_000_000,
        "max_pll_out_hz": 80_000_000,
        "apb1_max_hz": 40_000_000,
        "apb2_max_hz": 40_000_000,
        "note": "Max SYSCLK 80 MHz. Low-power optimized.",
    },
}


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



def check_peripheral_exists(mcu_name: str, peripheral: str) -> tuple[bool, str]:
    """Check if a peripheral instance exists on the given MCU.
    Returns (exists: bool, message: str).
    """
    normalized_mcu = mcu_name.strip().upper().replace(" ", "").replace("_", "")
    normalized_periph = peripheral.strip().upper().replace(" ", "")

    # Find family
    family = None
    for prefix in ["STM32H7", "STM32F7", "STM32F4", "STM32F3", "STM32F2", "STM32F1",
                   "STM32F0", "STM32G4", "STM32G0", "STM32L4", "STM32L1", "STM32L0",
                   "STM32WB"]:
        if normalized_mcu.startswith(prefix):
            family = prefix
            break

    if family is None or family not in MCU_FAMILY_PERIPHERALS:
        return True, f"No peripheral data for {mcu_name} — cannot verify, proceed with caution."

    periphs = MCU_FAMILY_PERIPHERALS[family]

    # Determine category
    cat = None
    if re.search(r'^TIM\d+$', normalized_periph, re.IGNORECASE):
        cat = "tim"
    elif re.search(r'^(USART|UART)\d+$', normalized_periph, re.IGNORECASE):
        cat = "usart"
    elif re.search(r'^I2C\d+$', normalized_periph, re.IGNORECASE):
        cat = "i2c"
    elif re.search(r'^SPI\d+$', normalized_periph, re.IGNORECASE):
        cat = "spi"
    elif re.search(r'^ADC\d+$', normalized_periph, re.IGNORECASE):
        cat = "adc"
    elif re.search(r'^DAC\d+$', normalized_periph, re.IGNORECASE):
        cat = "dac"
    elif re.search(r'^CAN\d+$', normalized_periph, re.IGNORECASE):
        cat = "can"
    elif normalized_periph in ("SDIO",):
        cat = "sdio"
    elif "USB" in normalized_periph:
        cat = "usb"
    else:
        return True, f"Unknown peripheral category for {peripheral} — cannot verify."

    if cat is None or cat not in periphs:
        return True, f"Unknown peripheral category — cannot verify."

    available = periphs[cat]
    if not available:
        return False, f"{mcu_name} ({family}) does NOT have {cat.upper()} peripherals. {peripheral} is unavailable."

    if normalized_periph.upper() not in [p.upper() for p in available]:
        available_str = ", ".join(available)
        return False, f"{peripheral} does NOT exist on {mcu_name} ({family}). Available {cat.upper()}: {available_str}"

    return True, f"{peripheral} exists on {mcu_name} ({family})."


def format_mcu_capability_summary(mcu_name: str) -> str:
    """Generate a concise capability summary for prompt injection.
    Uses progressive disclosure: only shows data for the relevant MCU family.
    """
    normalized_mcu = mcu_name.strip().upper().replace(" ", "").replace("_", "")

    # Find family
    family = None
    for prefix in ["STM32H7", "STM32F7", "STM32F4", "STM32F3", "STM32F2", "STM32F1",
                   "STM32F0", "STM32G4", "STM32G0", "STM32L4", "STM32L1", "STM32L0",
                   "STM32WB"]:
        if normalized_mcu.startswith(prefix):
            family = prefix
            break

    if family is None or family not in MCU_FAMILY_PERIPHERALS:
        return (
            f"WARNING: No peripheral reference data for {mcu_name}. "
            "Before writing code that uses any peripheral, verify it exists on this MCU. "
            "Do NOT assume peripheral availability."
        )

    periphs = MCU_FAMILY_PERIPHERALS[family]
    clocks = MCU_FAMILY_CLOCKS.get(family.replace("STM32", ""), {})

    lines = [f"### {family} MCU Capability Reference"]
    if clocks:
        max_sysclk = clocks.get("max_pll_out_hz", 0) // 1_000_000
        lines.append(f"- Max SYSCLK: {max_sysclk} MHz, HSI: {clocks.get('hsi_hz', 0)//1_000_000} MHz")

    for cat, instances in periphs.items():
        cat_upper = cat.upper()
        if instances:
            lines.append(f"- {cat_upper}: {', '.join(instances)}")
        else:
            lines.append(f"- {cat_upper}: NONE (not available on this family)")

    lines.append("- Before writing code using any peripheral, verify the instance name is in the list above.")
    lines.append("- NEVER write code targeting a peripheral NOT listed above.")
    return "\n".join(lines)

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