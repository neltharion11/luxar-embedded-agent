# Luxar

Luxar is an STM32-first embedded AI agent toolkit for firmware generation, review, build, flash, monitor, and debug workflows.
It is converging on a Codex-style single-entry experience for embedded systems: you tell it what you want in natural language, and it routes the task into planning, review, generation, build, flash, monitor, or debug recovery.

## Main Entry

Recommended usage:

```powershell
luxar --project DirectF1C "Blink LED and print over UART"
luxar --project DirectF1C --plan-only "Read BMI270 over SPI and show the wiring plan"
luxar run --project DirectF1C --doc workspace\docs\bmi270.pdf --task "Generate the project and explain the required pins"
```

Expert commands are still available when you want them, including:

- `luxar forge`
- `luxar review`
- `luxar workflow driver`
- `luxar workflow debug`
- `luxar parse-doc`

## Workspace Paths

Luxar stores projects, toolchains, skills, and local working data under the repository `workspace/` directory.

- When you run Luxar from a source checkout, it automatically anchors paths to that checkout root.
- If you clone the repo to `D:\Dev\LUXAR`, Luxar uses `D:\Dev\LUXAR\workspace\...`.
- If you clone the repo to `E:\Projects\Tools\LUXAR`, Luxar uses `E:\Projects\Tools\LUXAR\workspace\...`.

For non-source or packaged installs, set one of these environment variables so Luxar knows where to read and write data:

```powershell
$env:LUXAR_ROOT="D:\Tools\LUXAR"
```

or

```powershell
$env:LUXAR_CONFIG="D:\Tools\LUXAR\config\luxar.yaml"
```

`LUXAR_ROOT` points to the project root. `LUXAR_CONFIG` points to the config file directly.

## Current Highlights

- Single-entry task routing through a shared `TaskRouter`
- Shared engineering document analysis for CLI, `forge`, and server/chat APIs
- STM32 firmware-mode project assembly
- Real `build -> flash -> monitor -> debug-loop`
- Review gate with custom embedded rules, `clang-tidy`, and semantic review
- Driver generation, reuse, storage, and protocol skill evolution

## Review Auto-Fix

Luxar can automatically apply small, low-risk fixes in `App/` files before or during build-style workflows.

Current default behavior:

- `review.auto_fix_enabled: true` enables automatic review-driven fixes.
- `review.auto_fix_rule_ids` controls which review rules are allowed to auto-fix.
- The default whitelist is `EMB-003` and `EMB-004`.
- Auto-fix is intentionally conservative: it is limited to `App/` files and only runs for configured rules.

Example config in `config/luxar.yaml`:

```yaml
review:
  enabled: true
  auto_fix_enabled: true
  auto_fix_rule_ids: [EMB-003, EMB-004]
  fail_on_warning: false
  max_fix_iterations: 3
```

What the defaults mean:

- `EMB-003`: missing Doxygen-style comments
- `EMB-004`: disallowed `printf` usage in review-scoped embedded code

Recommended guidance for custom whitelists:

- Usually safe to auto-fix:
  `EMB-003` missing Doxygen comments, `EMB-004` disallowed `printf`
- Usually needs human review first:
  `EMB-001` direct CubeMX global handle usage, `EMB-005` missing NULL checks,
  `EMB-006` hardcoded register addresses, `EMB-007` blocking HAL calls in ISR,
  `EMB-010` missing matching header
- Usually refactor-level and best kept manual:
  `EMB-008` dynamic allocation warnings, `EMB-009` complexity warnings
- Never treat CubeMX structure issues as routine auto-fix:
  `EMB-002` should normally be resolved by regeneration or careful manual repair

Quick rule reference:

| Rule | Meaning | Auto-fix recommendation |
| --- | --- | --- |
| `EMB-003` | Missing Doxygen-style comment | Safe default |
| `EMB-004` | `printf` in review-scoped embedded code | Safe default |
| `EMB-005` | Pointer parameter not validated | Manual unless your team wants aggressive fixes |
| `EMB-006` | Hardcoded peripheral register address | Manual |
| `EMB-007` | Blocking HAL call in ISR | Manual |
| `EMB-008` | Dynamic allocation detected | Manual |
| `EMB-009` | Function complexity too high | Manual |
| `EMB-010` | Missing matching header file | Manual |

Common adjustments:

- Disable all automatic fixing:

```yaml
review:
  auto_fix_enabled: false
```

- Keep automatic fixing on, but change the whitelist:

```yaml
review:
  auto_fix_enabled: true
  auto_fix_rule_ids: [EMB-003, EMB-004, EMB-005]
```

- Keep review enabled, but do not auto-fix anything:

```yaml
review:
  auto_fix_enabled: true
  auto_fix_rule_ids: []
```

When auto-fix is active, Luxar will attempt the fix, re-run review, and then continue with build if the blocking review issues are resolved.

## Status

See [CURRENT_STATUS.md](CURRENT_STATUS.md) for the current implementation snapshot and remaining roadmap items.



