---
name: oled-ch1116
category: bringup
promotion_level: draft
triggers:
  - "OLED stays dark after flash"
  - "Need CH1116 bring-up before integration"
verification:
  - "Run a minimal I2C/OLED harness first"
---

# OLED CH1116 Bring-up

## When To Use

Use this skill when a CH1116 OLED must be integrated or debugged. Prefer it before generating full application UI behavior.

## Procedure

1. Confirm reset assumptions and I2C address expectations.
2. Run the matching OLED bring-up harness before integrating RGB status output.
3. Only merge display logic into the app after the bring-up harness captures evidence.

## Pitfalls

- Build success does not prove the panel acknowledged the bus.
- Some modules do not require explicit RST wiring.
- Address and initialization variants can differ across boards.

## Verification

- The selected harness records build, flash, and bring-up evidence.
