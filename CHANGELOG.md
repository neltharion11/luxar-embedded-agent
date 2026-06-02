# Changelog

## v0.2.3 - 2026-06-02

- Added STM32 hardware gate support with `workspace_hw_probe` for ST-Link/SWD evidence.
- Added explicit UART gate firmware generation after confirmed USART, TX/RX pins, and baudrate.
- Improved STM32Firmware baremetal and FreeRTOS templates for VSCode build/flash/debug flows.
- Generalized STM32 firmware package resolution beyond hardcoded F103 startup/linker assets.
- Reduced chat UI noise by hiding successful read/tool output and folding edited files.
- Updated CubeMX project creation behavior to keep CubeMX-generated code and toolchain ownership separate.
- Added regression tests for hardware gate tools, chat UI noise reduction, firmware templates, and debug monitor isolation.
