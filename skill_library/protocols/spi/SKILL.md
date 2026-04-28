# SPI Protocol Skill

## Scope

This skill captures reusable guidance for building and reviewing SPI-based embedded drivers. It should stay protocol-level and avoid board-specific private details.

## Applicability

- Protocol: SPI
- Validated device example: BMI270
- Platforms: stm32cubemx
- Runtimes: baremetal
- Validation source: DirectF1C

## Interface Pattern

- Prefer MCU-agnostic drivers with injected HAL callbacks.
- Keep transport operations explicit and timeout-aware.
- Return deterministic status codes from public APIs.
- Avoid direct references to CubeMX globals, dynamic allocation, and console I/O in the driver layer.

## Summary

validated bring-up

## Common Errors

- Missing null checks on public pointer parameters.
- Hardcoded platform handles or register constants leaking into reusable drivers.
- Blocking calls inside interrupt context.
- Missing reset, chip-select, or timing guard steps during initialization.

## Debug Checklist

- Confirm bus mode, frequency, and timing before first transaction.
- Validate reset and bring-up sequencing from the datasheet.
- Read a stable identity or status register before enabling advanced features.
- Keep logging and diagnostic hooks above the pure driver layer.

## Boundary Conditions

- Check chip-select timing.
