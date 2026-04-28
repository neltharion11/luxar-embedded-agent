# Luxar Current Status

## Snapshot

Luxar is currently a STM32-first embedded AI engineering toolkit with a working local hardware loop, a usable review gate, driver-generation workflows, local knowledge reuse, and a basic web dashboard.

Current branding:
- Product name: `Luxar`
- Python package: `luxar`
- Primary CLI command: `luxar`

## Current Product Shape

Luxar now has a single-entry CLI path in addition to the existing expert commands:

- `luxar --project <name> "<task>"`
- `luxar run --project <name> --task "<task>"`

This entry routes natural-language requests through a shared `TaskRouter`, a shared document-engineering analysis layer, and the existing internal workflows (`forge`, `workflow driver`, `workflow debug`, `review`, `fix`, and project status tooling).

## Feature Completion Matrix

| Capability | Status | Current Reality |
| --- | --- | --- |
| Intelligent document parsing | Partially implemented | `parse-doc`, register/pin table extraction, local KB, and query retrieval work; timing-diagram understanding and stronger protocol extraction are still incomplete. |
| MCU-agnostic driver library | Partially implemented | Driver generation, storage, search, reuse, and reuse scoring exist; true interface-injection consistency and validated cross-platform reuse are not fully complete yet. |
| Mandatory Review Layer | Implemented | Static analysis, custom rules, semantic LLM review, and build-time quality gating are already present with safe degradation paths. |
| Closed-loop debug | Mostly implemented | Real `build -> flash -> monitor -> debug-loop` works for STM32, including compile-fix, selected link repair, retry/review/rebuild, and serial diagnosis; deeper self-healing is still in progress. |
| Natural language to complete project assembly | Mostly implemented | `luxar forge` now performs structured project planning, driver reuse-or-generation, project assembly, App generation, review/fix, build, and graceful flash/monitor skipping when probe/port are absent; it still needs deeper hardware inference and stronger non-STM32 coverage. |

## Completed

### Stage 0: Project Scaffold
- Python package scaffold is in place.
- Editable install works.
- CLI entrypoint exists and is usable.

### Stage 1: Local Project Foundations
- Project initialization works.
- Config loading works from `config/luxar.yaml`.
- Logging, lock, backup, and Git helpers are implemented.
- New projects include a default `.clang-tidy`.
- Workspace path resolution now consistently anchors to the Luxar project root.
- `luxar config workspace` now shows the resolved workspace root and discovered projects.

### Stage 2: STM32 Bring-Up and Local Hardware Loop
- STM32 firmware-mode project assembly works.
- Bundled toolchain discovery works for `cmake`, ARM GCC, `ninja`, and STM32 Programmer CLI.
- Real build succeeds on `DirectF1C`.
- Real flash succeeds through ST-Link.
- Real serial monitor succeeds on `COM3`.
- `debug-loop` completes end-to-end and releases the serial port automatically.

### Stage 3: Review Gate
- `luxar review` works on a file or whole project.
- Layer 2 custom rule engine is implemented.
- `clang-tidy` integration is wired in with safe fallback when the tool is unavailable.
- `luxar build` is review-gated by default.
- `luxar build --skip-review` bypasses the gate for hardware iteration.
- Review engine boundary tests and build-gate tests are in place.

### Stage 4 Foundation: Generation, Fix, Workflow, and STM32 Recovery
- A single-entry task router now classifies natural-language requests into `explain`, `forge_project`, `generate_driver`, `review_or_fix`, `debug_project`, and `project_status`.
- CLI single-entry execution now supports:
  - `luxar --project <name> "<task>"`
  - `luxar run --project <name> --task "<task>"`
- Shared `EngineeringContext` models now capture:
  - `pin_requirements`
  - `bus_requirements`
  - `protocol_frames`
  - `register_hints`
  - `bringup_sequence`
  - `timing_constraints`
  - `integration_notes`
  - `risk_notes`
- A shared `DocumentEngineeringAnalyzer` now feeds:
  - CLI single-entry routing
  - `luxar forge`
  - server/chat document analysis APIs
- `luxar forge` now consumes the shared engineering-context path instead of maintaining a separate document-summary-only path.
- Unified `LLMClient` exists for `deepseek`, `openai`, and `claude`.
- Prompt templates live under `src/luxar/prompts/`.
- Minimal `generate-driver` flow is implemented.
- Generated driver output is parsed from header/source code blocks and written to disk.
- Minimal `fix-code` flow is implemented and can apply or dry-run LLM-proposed fixes for a reviewed file.
- Minimal `generate-driver-loop` implements `generate -> review -> fix` retries for driver files.
- Drivers that pass the pipeline can be stored into a local SQLite-backed driver library.
- `search-driver` queries the local driver index.
- Driver generation consults local driver library, knowledge base, and protocol skills before prompting the LLM.
- Driver generation can directly reuse an existing reviewed driver when it finds an exact or sufficiently confident match.
- Reuse confidence scoring is implemented with weighted scoring and persisted reuse statistics.
- Internal workflow orchestration exists for driver and debug flows through `luxar workflow ...`.
- `luxar forge` now includes a structured planning layer before code generation.
- `luxar forge` derives a `ProjectPlan` with features, needed drivers, peripheral hints, configuration actions, app behavior summary, and risk notes.
- `luxar forge` fallback planning can now detect multiple external devices and mixed protocol requests such as `BMI270 over SPI` plus `SHT31 over I2C`.
- `luxar forge` can run in `--plan-only` mode and return the structured plan without writing files.
- `luxar forge` automatically resolves driver requirements by reusing reviewed local drivers first and then invoking the existing driver pipeline for missing drivers.
- `luxar forge --drivers` now supports richer override forms such as `bosch/BMI270@spi`, `i2c:SHT31`, and `<chip>:<protocol>`.
- `luxar forge` upgrades App generation to use the structured project plan instead of a raw prompt string.
- `luxar forge` now uses explicit workflow steps: `plan -> resolve_drivers -> reuse/generate -> assemble -> generate_app -> review -> fix -> build -> flash -> monitor`.
- `luxar forge` defaults to the full closed loop and degrades gracefully when no probe or serial port is provided.
- Driver workflow is aligned with `retrieve -> decide -> reuse|generate -> review -> fix -> store -> skill`.
- Driver workflow has a LangGraph-backed path with safe fallback when `langgraph` is unavailable.
- Debug workflow has the same LangGraph-backed path with safe fallback.
- STM32 debug workflow performs limited automatic recovery actions such as clean-build retry and transient flash/monitor retries.
- STM32 debug workflow classifies failure types before choosing recovery actions.
- STM32 debug workflow can attempt build-aware source repair from compiler diagnostics.
- STM32 debug workflow distinguishes common link failures such as missing linker script, startup/runtime symbols, and entry-point gaps.
- STM32 debug workflow can restore missing firmware-mode scaffold files for selected link failures before rebuild.
- Automatic build repair now goes through a post-fix review gate before rebuild.
- `workflow debug` exposes granular recovery steps such as `build_fix`, `build_fix_review`, `build_retry`, `flash_retry`, and `monitor_retry`.
- `DebugLoop` has dedicated unit coverage for `run()` paths, diagnose/classify logic, compiler-error extraction, and project-config loading.

### Stage 5 Foundation: Driver Library
- Local SQLite-backed driver library exists.
- Driver storage and search work.
- Reuse statistics such as `reuse_count`, `last_reused_at`, and `kb_score` are persisted.
- Assemble-time installation of stored drivers into projects works.

### Stage 6 Foundation: Document Parsing and Local Knowledge Base
- `parse-doc` exists.
- Lightweight document parsing works for `.txt` and `.md`.
- `.pdf` parsing is supported through optional extractors.
- Parsed documents can be chunked into a local SQLite-backed knowledge base.
- Query-based snippet retrieval exists for protocol and register lookups.
- Knowledge-base retrieval participates in generation-time reuse context.
- PDF parsing can extract structured register and pin tables in supported cases.
- Register hex addresses are extracted as searchable keywords.
- Table chunks are preserved in structured text form for later reuse.

### Stage 7 Foundation: LLM Transport Resilience
- Provider-level resilience is implemented.
- `LLMClient._post_json()` uses retry/backoff behavior for HTTP 429, 5xx, and network errors.
- Retry parameters are configurable through `LLMSection` and `config/luxar.yaml`.

### Stage 8 Foundation: Skill Evolution
- `SkillManager` exists.
- `update-skill` can write and update protocol skills under `workspace/skill_library/protocols/`.
- `workflow driver` can automatically update a protocol skill after a successful run when evolution is enabled.
- Protocol skills are reused as generation context for later drivers.

### CLI Extension
- `list-skills` command exists.
- `status --project <name>` command exists.
- CLI coverage includes config commands, main commands, flags, and error paths.

### Web Dashboard (Stage 10 Foundation)
- A dialog-first web dashboard exists.
- Frontend layout includes sidebar navigation and project-aware chat flow.
- Sidebar now includes lightweight `new project` and `open existing project` actions.
- Existing local project directories can now be registered into the workspace index without being moved.
- Chat page supports Markdown rendering, highlighted code blocks, quick actions, and streaming responses.
- Chat now defaults to the same single-entry task orchestration used by the CLI, instead of starting from a separate tool-first flow.
- Chat now supports lightweight task context controls for optional document paths plus `plan only`, `no flash`, and `no monitor` execution toggles while preserving the existing visual style.
- Chat now renders structured execution summaries beneath assistant replies, including execution path, workflow status, project-plan highlights, and engineering-context notes.
- Server/API now exposes:
  - `/api/run-task`
  - `/api/analyze-docs`
  - `/api/project-context/{name}`
- Backend exposes tool-calling endpoints for project status, build, flash, monitor, debug loop, review, fix, file listing, git status, project listing, toolchain status, and driver generation.
- Tool calling now also exposes unified tools for:
  - `run_task`
  - `analyze_document_engineering`
  - `project_context`
- Model Config page exists and can update the `llm` section of `luxar.yaml`.

## Verified Commands

These commands were explicitly verified during implementation:

```powershell
luxar review --project DirectF1C
luxar build --project DirectF1C --skip-review
luxar flash --project DirectF1C --probe stlink
luxar monitor --project DirectF1C --port COM3
luxar debug-loop --project DirectF1C --probe stlink --port COM3 --lines 5
luxar search-driver --keyword bmi270
luxar assemble --project DirectF1C --drivers bmi270
luxar workflow debug --project DirectF1C --port COM3
luxar workflow driver --chip BMI270 --interface SPI --doc-summary "summary"
luxar parse-doc --doc workspace\\docs\\bmi270.txt --query "interrupt status"
luxar update-skill --protocol spi --device BMI270 --summary "validated bring-up" --source-project DirectF1C
luxar config workspace
luxar forge --project DirectF1C --prompt "Blink LED once per second and print Hello Agent on UART"
python -m unittest discover -s tests -p "test_*.py" -v
luxar --project DirectF1C --plan-only "Blink LED and print over UART"
```

## Test Status

- Current full unit-test count: `184`
- Current full unit-test result: all passing

## Not Yet Done

### Stage 4
- Fully graph-native workflow orchestration for `generate/review/fix/store`
- Stronger automated repair coverage beyond current compile-fix and selected link-repair baseline

### Stage 5
- Richer driver metadata and versioning
- Library CRUD beyond store/search
- Better compatibility ranking for reuse decisions

### Stage 6
- Stronger PDF extraction coverage on real datasheets, especially multi-page tables and merged cells
- Vector embedding store and stronger semantic retrieval
- More powerful generation-time KB reuse

### Stage 7
- Semantic review policy tuning for different platforms and code classes

### Stage 8
- Richer cross-project skill merging and deduplication
- Project-success-triggered skill writing beyond driver workflow
- Higher-quality LLM-authored skill synthesis once API credentials are configured

### Stage 9
- ESP-IDF adapter
- Linux host adapter
- FreeRTOS/Linux-specific review rules

### Web Dashboard
- API key is not configured by default, so chat/tool calls will fail until a provider key is set
- Conversation persistence is not implemented yet
- No durable conversation history panel per project yet

## Recommended Next Step

The highest-value next step is:

1. Configure `DEEPSEEK_API_KEY` and verify end-to-end web chat plus tool calling.
2. Add conversation persistence for the web dashboard.
3. Continue STM32-first improvements in datasheet parsing, vector retrieval, and deeper build/diagnose/fix strategies.

