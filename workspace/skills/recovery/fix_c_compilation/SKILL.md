---
name: fix_c_compilation
category: recovery
mode: executable
promotion_level: validated
triggers: ["build failed", "compilation error", "static analysis failed", "header not found"]
verification: ["compiler passes without fatal errors"]
related_lessons: []
references: []
---

# Fix C Compilation and Analysis Errors

## When To Use

Use this skill when encountering GCC compilation errors, missing headers (e.g., STM32 HAL headers), or EMB static analysis issues in `.c` or `.h` files.

## Procedure

1. Inspect the target source code and the associated review/build report.
2. Formulate the repair payload utilizing the LLM code repair prompt below.
3. Pass the payload to the internal runtime repair worker.
4. Verify the build output or re-review report to ensure the issue is resolved.

## LLM Repair Instructions

When prompting the internal repair worker, inject the following domain knowledge as repair requirements:

- **Minimal Edits**: Only modify the problematic fragments. Maintain existing function names, file structure, and coding style.
- **Priority**: Always prioritize fixing `BUILD` compilation errors (e.g., fatal errors, undeclared identifiers) before addressing `EMB` static analysis rules.
- **STM32 HAL Knowledge**:
  - If the error states `#include "stm32f10x.h"` is not found → Replace it with `#include "stm32f1xx_hal.h"`.
  - If the error states a HAL type or function is undefined → Ensure the correct HAL component header is included.
  - If project evidence indicates HAL usage, avoid expanding direct register manipulation.
- **Output Format**: Ensure the worker outputs the entire, complete file content within a standard markdown code block without omiting unchanged lines.

## Pitfalls

- Do not attempt widespread refactoring; changing function signatures may break callers.
- Do not hallucinate missing header contents if the evidence is insufficient; instead, expose the unknown to the user.
