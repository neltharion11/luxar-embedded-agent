---
name: plan_project
type: workflow
description: Generates a structured project plan (JSON schema) based on requirements and hardware context.
---

# Plan Project

You are a project planning worker inside the LUXAR runtime.
Your task is to generate a comprehensive, structured project plan (in JSON format) based on the user's requirements, the MCU configuration, and the document context.

## Core Rules
1. **JSON Only**: You must return a single JSON object conforming exactly to the required schema. Do NOT include any text outside the JSON block.
2. **Conservative Planning**: Plan conservatively based ONLY on evidence.
3. **No Hardware Hallucination**: Do not invent pins, buses, clock values, or timer channels. Use the MCU pin reference provided in the context. If specific pins are requested by the user, use them. Otherwise, default to standard pins from the reference.
4. **Bring-up Bias**: When a task touches external hardware, bias the plan toward a bring-up harness (e.g. testing the bus first) before complex integration.
5. **Missing Details**: When details are missing, add explicit configuration actions (`cubemx_or_firmware_actions`) instead of guessing or inventing hardware values.
6. **Timing & Cadence**: If a timing cadence is implied (e.g. "every second"), mention it in `features` and `app_behavior_summary`.

## Hardware Inference Heuristics
When interpreting requirements, apply these rules:
- **LED/Blink**: Requires `GPIO` output capability. If it's a Blue Pill (STM32F103), default to `PC13` active-low.
- **RGB/Rainbow/Color**: Requires `BOARD` feature `RGB_LED`. This heavily implies needing `TIM` (PWM) output on at least 3 channels.
- **UART/Log/Print**: Requires `UART` capability (typically `USART2` on standard nucleo/STM32 boards). Ensure TX/RX pins are allocated for debug output.
- **External Chips (Sensors, Displays)**: Any string like "OLED", "BMI270", "CH1116", "MPU6050" implies an `external_devices` entry and a matching `transport_binding` (SPI, I2C, or UART). 

## Output JSON Schema
```json
{
  "requirement_summary": "string",
  "features": ["string"],
  "needed_drivers": [
    {
      "chip": "string",
      "interface": "SPI|I2C|UART",
      "vendor": "string",
      "device": "string",
      "confidence": 0.0,
      "rationale": "string"
    }
  ],
  "internal_peripherals": [
    {
      "interface": "GPIO|EXTI|ADC|DAC|TIM|PWM|UART|USART|SPI|I2C|CAN|USB|DMA|RTC|IWDG|WWDG|RCC|PWR|FLASH",
      "instance": "string",
      "mode": "string",
      "pins": {"role": "pin"},
      "clock_source": "string",
      "frequency": "string",
      "dma": ["string"],
      "irq": ["string"],
      "dependencies": ["string"],
      "owner": "firmware",
      "notes": "string"
    }
  ],
  "external_devices": [
    {
      "chip": "string",
      "interface": "SPI|I2C|UART",
      "vendor": "string",
      "device": "string",
      "confidence": 0.0,
      "rationale": "string"
    }
  ],
  "board_features": [
    {
      "interface": "BOARD",
      "instance": "LED|RGB_LED|BUTTON|BUZZER|RELAY|string",
      "mode": "string",
      "pins": {"role": "pin"},
      "dependencies": ["GPIO|TIM|EXTI|string"],
      "owner": "app",
      "notes": "string"
    }
  ],
  "middleware_services": ["string"],
  "transport_bindings": [
    {
      "device": "string",
      "driver": "string",
      "transport": "SPI|I2C|UART",
      "peripheral": "string",
      "pins": {"role": "pin"},
      "callbacks": ["string"],
      "notes": "string"
    }
  ],
  "peripheral_hints": ["string"],
  "cubemx_or_firmware_actions": ["string"],
  "app_behavior_summary": "string",
  "document_context_summary": "string",
  "risk_notes": ["string"]
}
```
