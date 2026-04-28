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

## Status

See [CURRENT_STATUS.md](CURRENT_STATUS.md) for the current implementation snapshot and remaining roadmap items.



