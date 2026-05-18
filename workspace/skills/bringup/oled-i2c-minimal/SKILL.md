---
name: oled-i2c-minimal
category: bringup
mode: executable
promotion_level: draft
triggers:
  - "OLED stays dark after flash"
  - "Need minimal OLED runtime validation"
verification:
  - "collect build evidence"
  - "collect flash evidence"
  - "collect probe or monitor evidence"
related_lessons: []
references: []
---

# OLED I2C Minimal

## When To Use

Use this executable skill when an OLED path needs the narrowest possible runtime validation before application-level integration.

## Procedure

1. Build the smallest OLED initialization path available for the project.
2. Flash the image and capture the flashing result.
3. Collect probe or monitor evidence before changing higher-level UI logic.

## Pitfalls

- Build success does not prove the display acknowledged the bus.
- Runtime evidence matters more than compile confidence.

## Verification

- Build evidence collected
- Flash evidence collected
- Probe or monitor evidence collected
