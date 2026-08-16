# LUXAR Enterprise Learning Progress

Updated: 2026-08-12

## Current milestone

The production-shaped `EspIdfCliAdapter` slice is complete. The unchanged seven-node Graph can now use real ESP-IDF project preflight, dependency authorization, `idf.py reconfigure/build`, sanitized build evidence, and structured diagnostics through the existing `EspIdfPort`.

## Verified work

- `src/` package imports through an editable installation.
- `FirmwareRequirement` validates the ESP-IDF platform boundary.
- Completeness is derived from explicit missing fields.
- Pydantic domain validation remains independent of LangGraph.
- Domain suite: `4 passed`.

## Learning history

The project now distinguishes an intended action (`ExecutionPlan`), an observed tool fact (`BuildEvidence`), and a normalized failure (`WorkflowError`). The complete Domain suite passes 15 tests.

The application-owned Ports for requirement parsing, planning, and ESP-IDF building are complete. Core layers contain no DeepSeek, OpenAI-compatible client, subprocess, LangGraph, or API-key dependencies.

Deterministic Fake Adapters now satisfy the three Ports, record calls, and provide controlled requirement, plan, and build evidence. Adapter suite: 4 passed; full suite: 19 passed.

`WorkflowState` and frozen `RuntimeContext` are complete. The State holds business progress; the Context injects Parser, Planner, ESP-IDF, and project path without provider leakage. Full suite: 20 passed.

The six original nodes now use `Runtime[RuntimeContext]` where external capabilities are required and return minimal State updates. All six nodes have direct execution tests. The complete suite last passed 26 tests before requirement routing; the requirement router then passed both complete and incomplete branch tests.

The learner identified that rebuilding unchanged source code is not a repair. The approved repair-loop design therefore adds structured `BuildDiagnostic` values, complete-file `RepairPlan` values, `RepairPlanner` and `WorkspacePort`, and one `repair_project` LangGraph node. Source/linker failures route through repair before rebuilding; timeout may directly retry; environment/unknown failures terminate.

Authoritative continuation documents:

- `docs/superpowers/specs/2026-08-01-luxar-repair-loop-design.md`
- `docs/superpowers/plans/2026-08-01-luxar-repair-loop-plan.md`

`BuildDiagnostic` is now part of `BuildEvidence`, preserving file, one-based line/column, severity, optional diagnostic code, and message. Focused evidence suite: 11 passed.

Safe complete-file repair models are complete. They normalize project-relative paths and reject empty, absolute, drive-qualified, parent-traversal, empty-plan, and duplicate-target inputs. Focused repair-domain suite: 13 passed.

Next: define `RepairPlanner` and `WorkspacePort`, then provide deterministic Fake implementations.

`RepairPlanner` and `WorkspacePort` plus their deterministic Fakes are complete. Adapter contract suite: 6 passed. State and Runtime Context now carry repair results and inject repair capabilities respectively.

The learner implemented `repair_project`. Its direct test proves the node reads project files, passes requirement/plan/evidence/files to the repair planner, applies the returned repair, preserves the previous evidence, and does not increment build attempts. Node suite: 7 passed.

Next: implement the pure evidence-driven build route.

The evidence-driven build route is complete. Twelve route tests cover complete/incomplete requirements, success on the final allowed attempt, source/linker repair, timeout retry, environment/unknown failure, missing category, and exhausted budgets. Full suite: 59 passed.

Next: compile the seven business nodes and their ordinary/conditional edges into the first executable `StateGraph`.

## Current verified checkpoint

The seven-node `StateGraph` is compiled and tested. Integration tests prove clarification, timeout retry without repair, source failure followed by structured repair and successful rebuild, environment failure without repair, final evidence preservation, exact Port call counts, and streaming node order.

Verification command:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
```

Result: 65 passed. Core-layer searches found no old learning-package imports and no provider/tool implementation leakage.

Next: implement the DeepSeek Adapter slice, beginning with a shared OpenAI-compatible DeepSeek client boundary and mocked structured-output tests for requirement parsing, planning, and repair planning.

The DeepSeek Adapter architecture is now approved. It uses the `openai` Python package only as an OpenAI-compatible transport pointed at `https://api.deepseek.com`, with current `deepseek-v4-flash` and `deepseek-v4-pro` models. Default tests remain offline; one real requirement-parsing smoke test is explicitly opt-in.

Authoritative DeepSeek documents:

- `docs/superpowers/specs/2026-08-01-luxar-deepseek-adapters-design.md`
- `docs/superpowers/plans/2026-08-01-luxar-deepseek-adapters-plan.md`

Next: add settings, dependencies, and the stable capability-error boundary.

DeepSeek Task 1 is complete. `DeepSeekSettings` reads a secret-safe key and current model defaults from environment variables; `CapabilityError` provides seven provider-independent failure categories. Focused suite: 12 passed; full offline suite: 77 passed.

Next: implement the shared `JsonCompletionClient`, its deterministic Fake, and the production DeepSeek JSON wrapper.

All 30 Python source files now begin with a Chinese module description and include focused beginner comments for validation, typing, dependency injection, State updates, routing, Graph composition, and Fake behavior. `FakeJsonCompletionClient` was also moved into the planned `adapters/deepseek/` package.

`DeepSeekJsonClient` is complete. It sends OpenAI-compatible JSON-mode requests to the configured DeepSeek endpoint, disables hidden SDK retries, rejects empty/invalid JSON responses, and normalizes authentication, timeout, rate-limit, connection, 4xx, and 5xx failures without leaking sensitive SDK messages. Client suite: 13 passed; full offline suite: 93 passed.

Next: implement `DeepSeekRequirementParser`, the first business Adapter that converts a JSON dictionary into a validated LUXAR Domain object.

`DeepSeekRequirementParser` is complete. It sends the current Pydantic JSON Schema and JSON-encoded user task through the configured fast model, preserves explicit missing fields, and converts unsupported/missing/mistyped output into a sanitized `invalid_schema` capability error. Parser suite: 6 passed; full offline suite: 99 passed.

Next: implement `DeepSeekPlanner`, converting a validated requirement into a validated ordered `ExecutionPlan`.

`DeepSeekPlanner` is complete. It serializes the validated requirement, sends the generated `ExecutionPlan` schema through the selected fast model, preserves ordered supported actions, and rejects empty, unknown, or incomplete steps through a sanitized `invalid_schema` error. Planner suite: 5 passed; full offline suite: 104 passed.

Next: implement `DeepSeekRepairPlanner`, carrying build diagnostics and validated project files into a complete-file `RepairPlan`.

`DeepSeekRepairPlanner` is complete. It sends validated requirement/plan/evidence/file data through the configured repair model, explicitly treats logs and source as untrusted data, and validates complete-file replacements through the existing path invariants. Tests prove absolute paths, parent traversal, duplicate targets, and empty repairs are rejected even when the model violates the prompt. Repair Adapter suite: 6 passed; full offline suite: 110 passed.

Next: add the composition root that constructs one shared DeepSeek client and injects the three production model Adapters into the unchanged Runtime Context and Graph.

The DeepSeek composition root is complete. It creates or accepts one shared JSON Client, assigns the fast model to requirement parsing and execution planning, assigns the repair model to evidence-driven repair, and injects those Adapters together with the ESP-IDF and Workspace Ports into `RuntimeContext`. Offline wiring tests prove exact object identity and model selection; the unchanged Graph topology test still passes. Focused suite: 3 passed.

Next: add the explicitly opt-in real DeepSeek smoke test, then design the smallest centralized `CapabilityError` to `WorkflowError` application boundary before learner implementation.

The centralized model-capability error boundary is complete. `run_workflow()` consumes full State snapshots, catches `CapabilityError` once outside the seven business nodes, maps it to fixed application-owned `WorkflowError` text, and returns an explicit failed State. Tests prove requirement, plan, build evidence, diagnostics, and attempts survive failures at later stages. Runner suite: 14 passed.

The optional real DeepSeek requirement-parser smoke test is present and guarded by both `DEEPSEEK_API_KEY` and `LUXAR_RUN_DEEPSEEK_SMOKE=1`. It was collected but intentionally not sent. Final complete offline verification for the DeepSeek Adapter slice: 130 passed, 1 skipped. Dependency searches found exactly one application `CapabilityError` catch and only one `openai` import location under the DeepSeek client package.

Next enterprise slice: implement a path-contained `LocalWorkspaceAdapter`, then a real ESP-IDF CLI Adapter, while continuing to teach the complete Agent call chain through production-quality code.

A consolidated Chinese review guide now provides one learning entrypoint for the completed material. It maps English Agent/LangGraph architecture terms to Chinese explanations and real source files, explains the Python syntax used in LUXAR, traces four vertical workflow paths, and summarizes tests, safety boundaries, common misconceptions, and a one-page review checklist. The existing `01` through `07` notes remain as deeper topic chapters.

## Local Workspace Adapter checkpoint

Authoritative documents:

- `docs/superpowers/specs/2026-08-04-luxar-local-workspace-adapter-design.md`
- `docs/superpowers/plans/2026-08-04-luxar-local-workspace-adapter-plan.md`
- `docs/learning/08-local-workspace-adapter.md`

The production-shaped `LocalWorkspaceAdapter` now implements `WorkspacePort`.
It scans a fixed ESP-IDF source/configuration allowlist in deterministic order,
prunes generated and tool-owned directories, rejects binary or non-UTF-8 text,
and enforces default 256 KiB per-file and 1 MiB aggregate budgets.

Domain path normalization is reinforced by strict resolved-path containment and
symlink/Windows Junction checks immediately around I/O. Complete-file repairs
can modify only existing allowlisted files. They validate all targets, stage all
new contents in same-directory temporary files, commit with `os.replace`, and
reverse-roll back already committed targets after a handled later failure.
Rollback failure has an explicit non-retryable category.

`WorkspaceError` provides eight stable filesystem-independent categories. The
single application Runner boundary catches model and workspace capability
errors, maps them to sanitized `WorkflowError` values, and preserves the latest
requirement, plan, build evidence, diagnostics, and attempt count. The seven-node
LangGraph topology is unchanged.

Latest complete verification before documentation-only synchronization:
`196 passed, 5 skipped`. The skips are the opt-in real DeepSeek smoke and four
ordinary symlink cases unavailable under current Windows permissions; Windows
Junction tests executed and passed.

Next enterprise slice: implement `EspIdfCliAdapter`. Its preflight validates the
ESP-IDF environment and project, runs `idf.py reconfigure`, verifies declared
dependencies, and only then builds. Dependency downloads remain disabled by
default and require explicit `allow_dependency_downloads=True` authorization.

## Current learning workflow

For the completed ESP-IDF slice, the learner explicitly selected “finish first,
then study”: Codex completes the approved production code, tests, documentation,
audit, and commits without interrupting implementation for copy-and-type
exercises. Teaching then works backward from the verified final code. Markdown
and progress records always remain Codex-owned.

## ESP-IDF CLI Adapter checkpoint

Authoritative documents:

- `docs/superpowers/specs/2026-08-04-luxar-espidf-cli-adapter-design.md`
- `docs/superpowers/plans/2026-08-12-luxar-espidf-cli-adapter-plan.md`
- `docs/learning/09-espidf-cli-adapter.md`

`EspIdfCliAdapter` now validates a real ESP-IDF project root, trusted command
launcher, manifest encoding/YAML/size, and link/Junction boundaries before
starting a process. Non-empty component dependencies are rejected before any
command unless the application explicitly sets `allow_dependency_downloads=True`.
Safe mode also sends `IDF_COMPONENT_MANAGER=0` in a copied child environment.

The Adapter runs `idf.py reconfigure` before `idf.py build` using a parameter
list, `shell=False`, validated `cwd`, captured UTF-8 output, separate timeouts,
and no hidden retry. It classifies dependency, environment, linker, source, and
unknown failures; extracts GCC/Clang and CMake diagnostics; removes ANSI and
external absolute paths; and bounds State summaries.

Pre-command failures become sanitized `EspIdfError` values at the single Runner
boundary. Completed or timed-out commands become factual `BuildEvidence`.
Bootstrap selects real Workspace and ESP-IDF Adapters by default while preserving
explicit Fake injection. The seven-node LangGraph topology is unchanged.

Final default verification: `269 collected, 261 passed, 8 skipped`. The real
DeepSeek and ESP-IDF smokes remain explicitly opt-in; unavailable ordinary
Windows symlink cases are skipped while Junction safety cases execute and pass.
Security searches found production `subprocess` only in the ESP-IDF Adapter, no
shell-string execution, no unsafe YAML loading, and one joint application error
boundary.

Next enterprise slice: add a real application/CLI entrypoint and explicit
configuration boundary that composes the completed DeepSeek, Workspace, and
ESP-IDF capabilities. This is not implemented by the current slice.

## CLI entrypoint checkpoint

Authoritative documents:

- `docs/superpowers/specs/2026-08-12-luxar-cli-entrypoint-design.md`
- `docs/superpowers/plans/2026-08-12-luxar-cli-entrypoint-plan.md`
- `docs/learning/10-cli-entrypoint.md`

The editable package now exposes `luxar run`. The CLI requires an explicit
project path, accepts or interactively asks for a task in ordinary mode, forbids
interactive JSON mode, and passes dependency-download authority only from an
explicit flag. Secrets remain environment-backed.

Runner progress is an immutable safe event containing only stage, fixed message,
and attempt count. Ordinary progress goes to stderr; final Chinese summaries go
to stdout. JSON mode installs no reporter and emits one allowlisted document.
Exit codes distinguish success, startup errors, clarification, workflow failure,
and Ctrl+C.

Editable installation and both help commands succeeded. Complete verification
after final synchronization: `290 collected, 282 passed, 8 skipped`.
Static searches found no Graph, subprocess, YAML, OpenAI client, or API-key
dependency in the CLI; the seven-node topology and single Runner capability
boundary remain intact.

Next enterprise slice: design LangGraph checkpoint persistence and human
approval before expanding the Agent with resumable interaction. No persistence
or approval is implemented in the CLI slice.

## Full-Pipeline checkpoint (2026-08-16)

Authoritative documents:

- `docs/superpowers/specs/2026-08-16-luxar-full-pipeline-design.md`
- `docs/superpowers/plans/2026-08-16-luxar-full-pipeline-plan.md`
- `docs/learning/11-full-pipeline.md`

Six slices (S1–S6) turned the build-only Agent into the complete
create → build → flash (human approval) → monitor → log-analysis pipeline.

S1 made `ExecutionPlan` the real execution contract: the step vocabulary is
`create_project/build_project/flash_project/monitor_project` with ordering
validators, and a cursor dispatcher node executes steps in order.

S2 added `ProjectEvidence`, `EspIdfProjectPort`, and `EspIdfProjectAdapter`
(`idf.py create-project` inside a contained parent directory, idempotent
reuse of existing projects, `CONFIG_IDF_TARGET` consistency in
`sdkconfig.defaults`). Shared preflight/sanitization internals moved to
`adapters/espidf_common.py`.

S3 added `FlashEvidence`, `EspIdfFlashPort`, platform-pattern port-name
validation, and human approval through LangGraph `interrupt()`. The runner
returns `WorkflowRunResult(thread_id, pending_approval)` and resumes with
`Command(resume=...)` against an injected `InMemorySaver`. Verified
langgraph 1.2.11 semantics: pause surfaces as a `__interrupt__` snapshot
key, never an exception; the runner strips it from business State.
Approval persists per run, so device-loop re-flashes never re-prompt.

S4 added `MonitorEvidence`, `DeviceLogDiagnostic`, `DeviceDiagnosis`,
`EspIdfMonitorPort` (bounded capture window, process-tree termination,
ESP32 failure-pattern extraction), `LogAnalystPort` and
`DeepSeekLogAnalyst` (logs treated as untrusted data). The device loop
repair → rebuild → re-flash → re-monitor is bounded by `device_cycles ≤ 3`;
`repair_origin` routes rebuilt firmware back through flash into monitor.
`RepairPlanner.create_repair` gained an optional `device_diagnosis`.

S5 added `luxar ports`, `--port`, interactive y/N approval (default
reject), a JSON-mode `--approve-flash` guard, an `approval` SSE event,
and `POST /api/conversations/{project}/approval` (strict contract, 409
without pending approval). `luxar ports` verified against the real CH340
board on COM4 (VID:PID=1A86:7523).

S6 added `docs/learning/11-full-pipeline.md`, security searches, and an
opt-in real device smoke (`tests/smoke/test_device_smoke.py`, gated by
`LUXAR_RUN_ESPIDF_SMOKE=1` + `LUXAR_ESP32_PORT`) that drives the real
ESP-IDF v6.0.2 toolchain found at `F:\esp\v6.0.2\esp-idf`: real
create-project, real build, real flash, and a real monitor capture window.

Latest complete offline verification: `493 passed, 8 skipped`.

Learning environment note: development uses the local conda env
`C:\Users\41562\.conda\envs\luxar-learning` with a sandbox workaround for
`os.mkdir` mode handling (`F:\LUXAR\.site-tools`, gitignored, loaded via
`PYTHONPATH`); `pyserial` is installed into that directory because the
sandbox denies writes into the conda environment.

