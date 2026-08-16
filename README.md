# LUXAR LangGraph

A clean, enterprise-shaped reconstruction of LUXAR using explicit domain models, Ports and Adapters, structured execution evidence, and LangGraph orchestration.

## Learning notes

Start with the Chinese [LUXAR Agent review guide](docs/learning/00-LUXAR-Agent-复习总览.md). It consolidates English-to-Chinese terminology, Python syntax used by this project, the layered architecture, end-to-end workflow paths, testing levels, and Agent safety rules. The numbered `docs/learning/01` through `11` files remain focused deep dives.

## Current milestone

The evidence-driven Agent now covers the complete firmware pipeline:

```text
natural-language task
  → validated FirmwareRequirement
  → validated multi-step ExecutionPlan (create → build → flash → monitor)
  → cursor dispatcher executes every step in order
  → project creation (idf.py create-project, contained)
  → build with the bounded repair loop
  → flash behind human approval (LangGraph interrupt/resume)
  → serial monitor capture window + AI log analysis
  → bounded device repair loop (repair → rebuild → re-flash → re-monitor)
  → completed, clarification, or sanitized failed State
```

LangGraph owns State transitions, conditional routing, the bounded repair
loops (`max_attempts`, `flash_attempts ≤ 2`, `device_cycles ≤ 3`), the
interrupt-based flash approval, and streaming. Domain models own validation.
Ports describe external capabilities. Runtime Context can inject either
deterministic Fakes or the production DeepSeek, workspace, and ESP-IDF
Adapters without changing the Graph topology.

The default suite uses no real model call, user-project write, serial port,
or `idf.py`. Hardware smokes are explicitly opt-in
(`LUXAR_RUN_ESPIDF_SMOKE=1` + `LUXAR_ESP32_PORT`). A centralized application
Runner converts model-side, workspace-side, and ESP-IDF capability failures
into sanitized failed State values while preserving the latest requirement,
plan, build evidence, flash evidence, monitor evidence, and diagnostics.

## Production entry chain

```text
build_deepseek_runtime_context(...)
  → RuntimeContext with one shared DeepSeek client
  → run_workflow(initial_state=..., context=...)
  → compiled LangGraph
  → completed / clarification / pending approval / sanitized failed
resume_workflow(thread_id, approved)  # after a flash approval pause
```

`build_deepseek_runtime_context(...)` selects concrete external capabilities.
`run_workflow(...)` is the production execution boundary and contains one
application-level handler for `CapabilityError`, `WorkspaceError`, and
`EspIdfError`. Business nodes remain provider and filesystem-implementation
independent.

## Core topology

```text
START → analyze_requirement
  ├─ incomplete → request_clarification → END
  └─ complete → create_plan → execute_next_step (plan cursor)
                                ├─ create_project → cursor
                                ├─ build_project → cursor
                                │    └─ source/linker → repair loop
                                ├─ flash_project → request_flash_approval
                                │    ├─ approved → flash → cursor
                                │    └─ rejected → failed
                                └─ monitor_project → analyze_device_logs
                                     ├─ healthy → completed
                                     └─ needs_repair → repair → build
                                          → re-flash → re-monitor (≤ 3)
```

The ESP-IDF Adapters validate projects and launchers, reject undeclared
network authority, run `idf.py` with bounded timeouts, sanitize output,
validate serial port names against platform patterns, and convert bounded
sanitized logs into evidence and diagnostics. Dependency downloads remain
forbidden by default and require explicit application-level authorization.
Flashing always requires human approval before the first flash of a run.

## Command-line entrypoint

After editable installation and activation of the configured environment:

```bat
luxar run --project C:\projects\blink --task "修复 ESP32 GPIO 工程" --port COM4
luxar ports
```

Omit `--task` in ordinary mode to enter it interactively. Flash approval is
interactive (`y`/`N`, default reject). Use `--json` only with an explicit task
for a stable machine-readable result; JSON mode requires `--approve-flash` to
pre-authorize flashing. Dependency downloads are forbidden unless
`--allow-dependency-downloads` is explicitly supplied. Model secrets remain
environment-backed and never appear as CLI arguments.

## Development environment

```bat
C:\Users\41562\.conda\envs\luxar-learning\python.exe -m pip install -e ".[dev]"
C:\Users\41562\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
```

## Optional real DeepSeek smoke

The default command skips the real API smoke test. To explicitly spend one
minimal DeepSeek request, provide `DEEPSEEK_API_KEY`, set
`LUXAR_RUN_DEEPSEEK_SMOKE=1`, and run:

```bat
C:\Users\41562\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/smoke/test_deepseek_requirement_parser.py
```

Never place the API key in source code, Markdown, State, prompts, or Git.

## Optional real ESP-IDF smoke

The default suite also skips the real ESP-IDF build and device operations.
Activate the ESP-IDF v6.0.2 environment, explicitly set
`LUXAR_RUN_ESPIDF_SMOKE=1` and `LUXAR_ESP32_PORT`, and run:

```bat
C:\Users\41562\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/smoke/test_espidf_cli.py
C:\Users\41562\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/smoke/test_device_smoke.py
```

The device smoke creates a dependency-free project under pytest's temporary
directory, builds it, flashes the connected board (replacing its current
firmware), and captures one monitor window. It never installs ESP-IDF,
automatically downloads components, or uses a user project.
