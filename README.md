# LUXAR LangGraph

A clean, enterprise-shaped reconstruction of LUXAR using explicit domain models, Ports and Adapters, structured execution evidence, and LangGraph orchestration.

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

## Web gateway

```bat
luxar web --projects-root F:\LUXAR\projects --serial-port COM4 --target esp32
```

Project root, serial port, and target chip are all selectable **per task in
the page**: the project root switches among the server's configured roots
(`--projects-root` may be repeated), the serial port is picked from the list
the server discovers live (`GET /api/devices/ports`), and the chip comes from
a fixed dropdown. Server-side values only act as defaults when the page
leaves a field unset. The browser still never submits arbitrary paths, port
names, or chip strings: roots must be configured at startup, ports must match
the platform pattern and the discovered list, and chips are strict lowercase
identifiers. Flash approvals surface as an in-page approval card
(`批准烧录` / `拒绝`) backed by `POST /api/conversations/{project}/approval`.

All entry points share one command shape:

```bat
luxar        :: bare invocation starts the Web gateway with env/default config
luxar run    --project DIR --task "..." [--port COM4] [--target esp32] [--json]
luxar ports
luxar web    --projects-root DIR [--serial-port COM4] [--target esp32]
luxar setup
luxar storage health
```

Bare `luxar` equals `luxar web` with values from `LUXAR_PROJECTS_ROOT`
(multiple roots separated by the platform path separator),
`LUXAR_SERIAL_PORT`, `LUXAR_TARGET_CHIP`, and `LUXAR_WEB_PORT`, falling
back to `./projects` and port 8000.

(`luxar-web` remains as a compatible alias for `luxar web`.)

## Embedded SQLite durability and LanceDB knowledge

LUXAR now uses an embedded local storage profile by default. SQLite stores
workflow runs, conversations, approvals, structured project memory, and
LangGraph `SqliteSaver` checkpoints. LanceDB stores knowledge documents,
chunks, metadata, and vectors. No Docker or database service is required.

See [STORAGE.md](STORAGE.md) for storage ownership, recovery, backup, and the
optional PostgreSQL compatibility profile.

```bat
copy .env.example .env
luxar storage health
luxar
```

`GET /api/health/database` reports `database=sqlite` and `durable=true`. Project
memories are structured JSON records exposed through
`GET|PUT /api/projects/{project}/memories`; only explicit device selections
are learned automatically. Raw conversations and model guesses are not
silently promoted to long-term memory.

The external knowledge API uses LanceDB vector search plus bounded lexical
reranking:

```text
POST /api/projects/{project}/knowledge/documents
POST /api/projects/{project}/knowledge/search
```

Set `LUXAR_EMBEDDING_API_KEY` (and optionally base URL/model/dimensions) to an
independent OpenAI-compatible embedding endpoint. Retrieved chunks carry
their title and source URI and enter requirement analysis as untrusted
reference context, not as executable instructions.

## Zero-config startup for new machines

One command covers both fresh machines and everyday startup:

```bat
powershell -ExecutionPolicy Bypass -File start.ps1
```

On a fresh clone, `start.ps1` performs the **complete** initialization in one
run: finds a Python 3.12 (offers a winget install when missing), creates the
`.venv`, installs the package with dev extras (including pyserial), generates
the gitignored `.env` (`DEEPSEEK_API_KEY`, project root, optional
`LUXAR_SERIAL_PORT`, `LUXAR_TARGET_CHIP`, `LUXAR_WEB_PORT`), **writes
`.venv\Scripts` into the user PATH automatically**, and detects an existing
ESP-IDF. It then starts the gateway immediately.

After that single run, a **new terminal** can use the CLI directly:

```bat
luxar                 :: start the gateway
luxar run --project ... :: run a firmware task
luxar ports           :: list serial ports
luxar setup           :: re-check the environment (instant when ready)
```

When an environment already exists (complete `.venv`, `LUXAR_PYTHON`, or any
Python with luxar importable), `start.ps1` skips setup entirely and starts the
gateway right away. `scripts\setup.ps1` remains available standalone
(`-Force` reinstall, `-NoPathEdit` skips the PATH write); `scripts\run-web.ps1`
is a thin alias of `start.ps1`.

Hardware note: LUXAR checks the ESP-IDF command when the Web gateway starts.
It searches `IDF_PATH`/`IDF_PYTHON_ENV_PATH`, Espressif Installation Manager
records, `idf.py` on `PATH`, common standard install directories, and the path
previously saved from the Dashboard. The Web UI shows a global warning when no
usable environment is found. The Dashboard can open a native folder picker for
an ESP-IDF root; that choice is saved under the first project root at
`.luxar/toolchain.json` and reused on later starts. A directory is considered
usable only when its `tools/idf.py --version` command actually succeeds, so an
incomplete ESP-IDF Python environment remains marked unavailable until it is
repaired.

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
