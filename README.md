# LUXAR LangGraph

A clean, enterprise-shaped reconstruction of LUXAR using explicit domain models, Ports and Adapters, structured execution evidence, and LangGraph orchestration.

## Current milestone

Stages 0–15 are implementation-complete, and the release baseline uses three
explicit runtime boundaries: qualified Supervisor firmware by default,
dedicated project-inspection/knowledge workflows, and an explicit legacy
firmware rollback path. On top of that baseline the repository now ships the
conversation-first Continuous Agent V2 as the default Web entry, specialized
read/knowledge workflows, runtime governance (qualification, observation and
retirement gates), a dashboard-configurable model layer, and deterministic
ESP-IDF grounding during code generation and repair. Legacy removal is not
yet qualified because the persisted 30-day observation window has not elapsed.

The qualified Supervisor Agent covers the complete firmware pipeline:

```text
natural-language task
  → ProjectObjective + ChangeSet + preserved capabilities
  → project/resource/component inspection
  → hardware validation
  → dependency-aware AgentTaskGraph
  → transactional task execution with bounded repair
  → acceptance verification backed by tool evidence
  → build → explicit flash approval → flash → monitor
  → completed / blocked / clarification / sanitized failed State
```

LangGraph owns State transitions, Supervisor routing, bounded task retries,
interrupt-based approval, recovery, and streaming. Domain models own
validation, preservation rules, hardware constraints, and acceptance criteria.
Ports describe external capabilities. Runtime Context can inject deterministic
Fakes or the production DeepSeek, workspace, and ESP-IDF Adapters without
changing the Graph topology.

The default suite uses no real model call, user-project write, serial port,
or `idf.py`. Hardware smokes are explicitly opt-in
(`LUXAR_RUN_ESPIDF_SMOKE=1` + `LUXAR_ESP32_PORT`). A centralized application
error boundary converts model-side, workspace-side, and ESP-IDF capability
failures into sanitized State values while preserving structured objectives,
task progress, evidence, and diagnostics.

## Production entry chain

```text
firmware task
  → select_firmware_runtime(...)
  ├─ qualified/default Supervisor
  │    → build_deepseek_agent_runtime_context(...)
  │    → run_agent_workflow(...) / resume_agent_workflow(...)
  └─ explicit legacy rollback
       → build_deepseek_runtime_context(...)
       → run_workflow(...) / resume_workflow(...)

inspection or knowledge task
  → build_deepseek_specialized_runtime_context(...)
  → run_specialized_workflow(...) / resume_specialized_workflow(...)
```

Every Runner contains the application-level error boundary for its own
workflow contract. Business nodes remain provider and filesystem-implementation
independent. Specialized workflows do not construct firmware execution ports.

## Supervisor topology

```text
START → load_project_session → supervisor
  ├─ project_inspector ────────┐
  ├─ hardware_validator ───────┤
  ├─ architecture_planner ─────┤→ supervisor (bounded loop)
  ├─ task_executor ────────────┤
  ├─ acceptance_verifier ──────┘
  └─ answer / complete / fail / degrade → END
```

## Legacy rollback topology

The following graph remains only for the explicit one-release firmware
rollback path and pre-migration checkpoint compatibility:

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
                                └─ flash monitor / monitor_project
                                     → analyze_device_logs
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

The Stage 10-qualified `supervisor` runtime is now the default for firmware
tasks. Set `LUXAR_AGENT_RUNTIME=legacy` for the one-release rollback path, or
set it explicitly to `supervisor`. Project inspection and knowledge tasks keep
their dedicated workflows; knowledge writes pause for explicit approval.
Device flashing follows
`build → approval → flash monitor → analyze_device_logs`; the production
adapter runs `idf.py -p PORT flash monitor` as one process so esptool and the
monitor never compete for the same serial port. CLI JSON mode only
pre-authorizes the `device.flash` operation when `--approve-flash` is present.

`GET /api/runtime` reports the selected firmware runtime, why it was selected,
the legacy deprecation/support window, and the evidence gates that still block
legacy removal. Dedicated project-inspection and knowledge workflows have
their own State, Graph, narrow Bootstrap, checkpoint Runner, result contract,
and `workflow_family`; they do not construct firmware execution ports and are
not counted as firmware rollback usage.

`GET /api/runtime/audit` performs a read-only retirement audit over persisted
run metadata, pending approvals, and the local SQLite checkpoint inventory.
The audit requires durable storage, a 30-day persisted observation baseline,
zero legacy firmware runs, zero ambiguous pre-migration records, and no active
recovery dependency before it can qualify as release evidence. It never reads
checkpoint payloads or exposes thread identifiers.

## ESP-IDF 工具链单一权威与 SDK 接地

编译、烧录与示例检索共用同一个权威解析（configured → environment →
installer → search），由 `EspIdfToolchainManager` 解析一次：在 Dashboard
选定的 ESP-IDF 根目录持久化到 projects 根目录的 `.luxar/toolchain.json`，
Web 与 CLI 的构建路径都遵守这个 pin，避免"网页选了一个版本、编译却用另
一个"的分叉。每次构建的 `BuildEvidence` 记录实际使用的 `idf_path` 与
`idf_version`（从 `tools/cmake/version.cmake` 读取，无子进程），下游修复
因此总能对齐权威源。

Supervisor 的修复路径在构建失败时用只读 SDK 探测把诊断接地到已安装的
ESP-IDF（不启动进程、不写文件）：

- 缺失头文件（`fatal error: X.h: No such file`）：在 components include
  树中判定存在性，并给出最接近的替代头文件（例如 ESP-IDF v6 移除了
  `driver/i2c.h`，探测会给出 `driver/i2c_master.h`）。
- API 改名/移除/弃用（`implicit declaration of function`、`is deprecated`、
  `unknown type name`、`no member named`）：在 `docs/en/migration-guides`
  中按符号名检索原文摘录，注入修复反馈。

两类提示都来自本次编译真正使用的 IDF 树，版本正确是构造性的；编译器
仍是最终裁判。

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

### Continuous Agent V2 (default Web entry)

Web conversations route through the conversation-first LangGraph Agent by
default. Set `LUXAR_CONTINUOUS_AGENT_V2=false` only as an emergency rollback to
the legacy / Supervisor entry path. The former project allowlist and shadow
rollout variables are ignored; routing is deliberately consistent across all
projects. `/api/runtime` and the conversation payload expose the resolved mode.

V2 keeps one durable Agent Session per active project conversation and creates
an independent Turn/SSE stream for every user message. The browser sends a
stable `session_id` plus a unique `client_turn_id`; duplicate client requests
replay the original Turn instead of repeating tools. The top model can answer,
request genuinely missing input, call typed tools, or delegate a complex
multi-file change to the existing Supervisor through `project.change`.
Workspace writes and device actions remain behind deterministic validation,
approval, and a persistent idempotency ledger. Waiting input and approvals can
resume from SQLite after a service restart.

The response headers expose `X-LUXAR-Session-ID` and `X-LUXAR-Turn-ID`;
`X-LUXAR-Thread-ID` remains the compatible Turn stream identifier. Session
state is available from `GET /api/conversations/{project}/session`, and a new
Session can be created with `POST /api/conversations/{project}/sessions`.
While a Turn is running, the browser can enqueue a follow-up instruction with
`POST /api/conversations/{project}/steer` or request a safe-boundary stop with
`POST /api/conversations/{project}/cancel`. Steering messages are durable and
idempotent; cancellation is checked before tool and domain-workflow side
effects, and it cancels only the current Turn/objective while leaving the
Session available for the next message.

All entry points share one command shape:

```bat
luxar        :: bare invocation starts the Web gateway with env/default config
luxar run    --project DIR --task "..." [--port COM4] [--target esp32] [--json]
luxar ports
luxar web    --projects-root DIR [--serial-port COM4] [--target esp32]
luxar setup
luxar storage health
luxar storage runtime-audit
luxar storage runtime-migration-plan
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

`luxar storage runtime-audit` prints the same privacy-safe retirement audit as
the Web endpoint. `luxar storage runtime-migration-plan` performs a read-only
dry run over pre-`workflow_family` SQLite records. It reports only aggregate
deterministic candidates and ambiguous counts; it does not modify metadata or
print task text/thread identifiers.

The external knowledge API uses LanceDB vector search plus bounded lexical
reranking:

```text
POST /api/projects/{project}/knowledge/documents
POST /api/projects/{project}/knowledge/search
GET  /api/projects/{project}/knowledge/documents
GET  /api/projects/{project}/knowledge/documents/{document_id}
DELETE /api/projects/{project}/knowledge/documents/{document_id}
POST /api/projects/{project}/knowledge/import-pdf
```

Set `LUXAR_EMBEDDING_API_KEY` (and optionally base URL/model/dimensions) to an
independent OpenAI-compatible embedding endpoint. Retrieved chunks carry
their title and source URI and enter requirement analysis as untrusted
reference context, not as executable instructions.

PDF imports are segmented by outline chapters first, with heading inference as
a fallback and bounded character subparts only for oversized chapters, so a
large manual is not silently truncated at one model-context limit. Text and
page-level visual metadata are always indexed. To interpret schematic or
drawing-only pages, configure `LUXAR_DOCUMENT_VISION_API_KEY`,
`LUXAR_DOCUMENT_VISION_BASE_URL`, and `LUXAR_DOCUMENT_VISION_MODEL` for an
OpenAI-compatible vision model.

## 模型配置

模型端点由 Dashboard 的模型设置面板保存到 projects 根目录的
`.luxar/model-config.json`（provider / base_url / model / timeout /
context window），密钥只落盘、不出现在读取响应中；未保存时回退到
`LUXAR_LLM_PROVIDER`、`LUXAR_LLM_BASE_URL`、`LUXAR_LLM_MODEL`、
`LUXAR_LLM_API_KEY` 等环境变量。provider 支持 deepseek / openai / local
（任意 OpenAI 兼容端点）。PDF 视觉解析支持 inherit / separate / python
三种模式；知识库 embedding 支持 local_hash（离线）与 api 两种模式。

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
firmware), and immediately captures the monitor window in the same `idf.py`
process. It never installs ESP-IDF,
automatically downloads components, or uses a user project.
