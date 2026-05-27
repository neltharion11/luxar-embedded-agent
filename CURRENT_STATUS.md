# LUXAR Current Status

Updated: 2026-05-20

## Snapshot

This file tracks the repository against `LUXAR0.2.0.md`, which defines the only accepted target architecture for LUXAR 0.2.0.

Current conclusion:

- The repository has already landed the core 0.2.0 skeleton: `agent/`, `skills/`, `memory/`, `policy/`, `domains/embedded/`, runtime-facing CLI commands, and runtime-facing API endpoints.
- The repository is still in a migration state, not a finished 0.2.0 state.
- Public 0.2.0 surfaces are present, but a large amount of legacy prompt-first and workflow-first implementation still exists under `core/`, `prompts/`, `tools/`, `server/`, and `workflows/`.
- The main project risk is no longer "missing 0.2.0 direction"; it is "old and new control planes coexisting in the same codebase".

Overall status: `Partially migrated to 0.2.0`

## Migration Matrix

| Area | Target in `LUXAR0.2.0.md` | Current Status | Notes |
| --- | --- | --- | --- |
| Runtime architecture | Unified runtime loop under `src/luxar/agent/` | Implemented baseline | `runtime.py`, `planner.py`, `context_builder.py`, `policy.py`, `promotion.py`, `explain.py` exist. |
| Skill-first artifact model | `workspace/skills/` + `src/luxar/skills/` are first-class | Implemented baseline | Skill registry/loader/matcher/manager/provenance exist, seeded skills are tested, protocol-skill writes target `workspace/skills/protocols/`, the repository no longer ships a default `workspace/skill_library/` tree, and legacy protocol skills are auto-imported into the new root with rewritten metadata paths when needed. |
| Lesson-first iteration | `workspace/lessons/` + lesson store/search/promotion | Implemented | `lesson_store.py` enforces strict lesson schema validation, and lesson CLI/API paths exist. |
| Durable memory and recall | `memory/` + `session_search` + transcript recall | Implemented baseline | `memory_manager.py`, `session_search.py`, `transcript_store.py`, `recall.py` exist, and durable memory now blocks obvious task-progress writes. |
| Thin public CLI | `luxar run`, `luxar skills ...`, `luxar memory ...`, `luxar workspace ...` | Implemented | New CLI surface is present and tests assert legacy commands are removed from the public CLI. |
| Thin public API | `/api/runtime/*`, `/api/skills/*`, `/api/memory/*`, `/api/workspace/*`, `/api/session-search` | Implemented baseline | These endpoints exist in `src/luxar/server/app.py`, and legacy project/document/firmware utility routes are no longer registered by default. |
| Immutable policy | `policy/immutable_policy.md` + executable rules | Implemented baseline | Immutable policy file exists; rules now include strict Mechanical Guardrails for lesson schemas and sandboxed dry-runs for executable skill promotion. |
| Evidence-driven execution | Build/flash/monitor/probe as workspace primitives | Implemented baseline | Build/flash/monitor are real wrappers, and `workspace_probe()` now returns structured STM32 configuration evidence for `i2c` / `spi` / `uart`. |
| Embedded domain routing | `domains/embedded/` task classification and routing | Implemented baseline | Capability map and routers exist. |
| Event model | phase, skill, lesson, promotion, escalation events | Implemented baseline | Streaming server emits these event families. |
| Prefix-stable thin prompt system | Thin permanent prompt + mechanical guardrails | Partially implemented | `workspace/prompts/system.md` exists, but `src/luxar/prompts/` legacy prompt modules still remain. |
| Legacy control plane deletion | Remove prompt-first/workflow-first main surfaces | Partially implemented | Public agent tool surface is now hard-limited to runtime/skills/memory/workspace primitives; task routing now reports vNext public intents directly; and the legacy `run_task` shell dispatches through dedicated runtime adapters instead of directly invoking workflow-era workers. |

## What Is Already Landed

### 1. Core 0.2.0 structure exists

The repository already contains the target-aligned directories described in `LUXAR0.2.0.md`:

- `src/luxar/agent/`
- `src/luxar/skills/`
- `src/luxar/memory/`
- `src/luxar/policy/`
- `src/luxar/domains/embedded/`
- `workspace/skills/`
- `workspace/lessons/`
- `workspace/memory/`
- `workspace/prompts/system.md`

Also confirmed on 2026-05-18:

- No source-level `src/luxar/harness/` directory is present.
- No `workspace/harnesses/` directory is present.

Also confirmed on 2026-05-20:

- No committed `workspace/skill_library/` directory remains in the repository default tree.

### 2. Public 0.2.0 CLI is in place

`src/luxar/cli.py` now exposes the 0.2.0-oriented command families:

- `luxar run`
- `luxar skills ...`
- `luxar memory ...`
- `luxar workspace ...`

Unit tests in [tests/unit/test_cli.py](/C:/Users/Gugugu/Documents/Codex/LUXAR/tests/unit/test_cli.py) explicitly assert that legacy public commands such as `forge`, `generate-driver`, `fix-code`, `review`, `workflow`, and `debug-loop` are no longer available on the main CLI surface.

### 3. Public 0.2.0 API surface exists

`src/luxar/server/app.py` currently exposes:

- `/api/runtime/run`
- `/api/runtime/explain`
- `/api/skills`
- `/api/skills/{name}`
- `/api/memory`
- `/api/memory/lessons`
- `/api/workspace`
- `/api/workspace/build`
- `/api/workspace/flash`
- `/api/workspace/monitor`
- `/api/workspace/probe`
- `/api/session-search`

This is directionally aligned with the 0.2.0 spec.

### 4. Runtime, skill, lesson, and recall baseline works

The repository already has:

- runtime orchestration entrypoint in `src/luxar/agent/runtime.py`
- runtime explanation path in `src/luxar/agent/explain.py`
- skill matching and listing via `src/luxar/skills/`
- executable skill dispatch via `src/luxar/tools/skills_tool.py`
- protocol skill evolution now writes to `workspace/skills/protocols/`
- protocol-skill reuse and listing paths now prefer `workspace/skills/protocols/`
- when only a legacy protocol skill exists, `SkillManager` now auto-imports it into `workspace/skills/protocols/` before continuing
- durable memory writes now reject obvious transient task-progress/status content instead of persisting it
- lesson recording/search/promotion via `src/luxar/memory/lesson_store.py` and `src/luxar/tools/memory_tool.py`
- transcript/session recall support in `src/luxar/memory/transcript_store.py` and `src/luxar/memory/session_search.py`

### 5. Embedded execution primitives still provide real value

Even though 0.2.0 wants a thinner control plane, the repository still benefits from real embedded workers already present behind the workspace/runtime wrappers:

- build
- flash
- monitor
- probe
- STM32-specific adaptation
- review/fix/debug/generation internals

These are useful as runtime workers and do not need to be deleted if they are absorbed behind the new runtime mental model.

## What Is Only Partially Done

### 1. Runtime is present, but still thin in behavior depth

The runtime loop exists, but it is currently a baseline orchestrator:

- it observes workspace layout
- matches skills
- searches lessons
- derives a plan
- returns diagnostics

What is still missing relative to the spec:

- deeper continue-or-escalate behavior
- stronger evidence gating logic inside the runtime itself
- richer lesson-to-skill patch/promotion loop
- tighter integration between runtime decisions and concrete embedded workers

### 2. Mechanical guardrails are not yet strong enough

The spec calls for architecture checks, promotion prechecks, runtime evidence checks, and stronger policy enforcement.

Current status:

- immutable policy text exists in [src/luxar/policy/immutable_policy.md](/C:/Users/Gugugu/Documents/Codex/LUXAR/src/luxar/policy/immutable_policy.md)
- tests cover parts of the new CLI/runtime behavior
- dedicated architecture guardrail tests now assert the vNext public API surface, deprecated API absence, and continued absence of misbuilt harness directories
- protocol-skill path handling is now centralized enough to distinguish new and legacy roots in config/runtime code
- durable memory writes now have a first mechanical guardrail against storing task-progress/status text
- some evidence-oriented behavior exists in workspace and skill execution paths

Still missing or incomplete:

- broader architecture tests that block every prompt-first regression path across `core/`, `tools/`, and server chat orchestration
- stronger promotion safety checks
- stronger proof that durable memory never accumulates task progress through indirect or structured write paths
- a real sandboxed dry-run gate for executable skill application/promotion

### 3. Workspace primitives still need deeper hardware evidence

Current state of the 0.2.0 `workspace` primitive family:

- `workspace_build()` is real
- `workspace_flash()` is real
- `workspace_monitor()` is real
- `workspace_inspect()` is real
- `workspace_probe()` is now a concrete STM32 configuration probe and returns structured evidence for configured `i2c` / `spi` / `uart` instances

What is still missing:

- probe execution is currently configuration-backed, not hardware-backed
- no bus scan, live register read, or runtime peripheral handshake evidence yet

So the evidence model is stronger than before, but still not fully complete for hardware bring-up tasks.

## What Still Blocks a True 0.2.0 Completion

### 1. Legacy main control plane is still in the repo

The spec is explicit that these areas should be deleted, absorbed, or demoted from public architecture status. They are still present:

- `src/luxar/core/`
- `src/luxar/prompts/`
- `src/luxar/workflows/`
- legacy-style tool modules in `src/luxar/tools/`
- large legacy-heavy orchestration inside `src/luxar/server/app.py`

Important nuance:

- The public CLI no longer exposes the old command families.
- Conversation-driven project creation no longer masquerades as an `init_project` agent tool call in streaming responses; it now emits dedicated project-creation events instead.
- `run_task` / `run_task_stream` now explicitly identify themselves as legacy compatibility entrypoints through warnings and result/event metadata, instead of looking like neutral runtime surfaces.
- `TaskRouter` now reports vNext-facing public intents/paths directly on `TaskIntent`, while preserving legacy workflow names only as compatibility metadata for the old `run_task` layer.
- `run_task` dispatch now goes through explicit runtime-worker adapter helpers, so legacy worker names are no longer referenced directly from the top-level compatibility routing branches.
- legacy HTTP utility routes such as `/api/projects*`, `/api/analyze-docs`, `/api/firmware-library`, `/api/conversations*`, and local file-picker endpoints are now behind the explicit `LUXAR_ENABLE_LEGACY_HTTP_SURFACE=1` opt-in switch instead of being part of the default API surface.
- the legacy HTTP/conversation registration logic has been physically extracted from `src/luxar/server/app.py` into `src/luxar/server/legacy_surface.py`, so the default app factory is no longer the place where those routes are implemented.
- legacy project-template parsing/creation helpers have also moved into `src/luxar/server/legacy_surface.py`, which further reduces `app.py` to vNext orchestration plus shared runtime helpers instead of mixed legacy endpoint logic.
- chat-context assembly and reasoning-handoff repair helpers have been extracted into `src/luxar/server/chat_support.py`, so `app.py` no longer defines the bulk of the conversation prompt/context support layer either.
- conversation store/cache lifecycle has been extracted into `src/luxar/server/conversation_state.py`, so `app.py` no longer owns `_conv_store`, `_conv_cache`, or the low-level get/save helpers directly.
- reasoning-handoff retry support now also lives in `src/luxar/server/chat_support.py`, so `app.py` only imports that recovery path instead of defining it locally.
- repeated agent-loop execution helpers for assistant/tool message appends, consecutive-failure accounting, and streaming tool-result event packaging now live in `src/luxar/server/agent_loop_support.py` instead of being duplicated inline inside `app.py`.
- loop-state bookkeeping and stream retry/final-message support have also been pulled into `src/luxar/server/agent_loop_support.py`, so the app-level loops now carry noticeably less duplicated state-management code.
- the remaining sync/stream agent-loop runner implementations now live in `src/luxar/server/agent_loop_runner.py`, with `app.py` reduced to thin wrappers that pass patchable dependencies into the runner.
- tool result envelopes, payload compaction/serialization, public tool validation, summary formatting, and synchronous public-tool execution now live in `src/luxar/server/tool_execution.py`, reducing `app.py` further toward routing plus thin wrappers.
- `run_task` compatibility warning/event/result envelope helpers now live in `src/luxar/tools/run_task_compat.py`, further reducing the legacy shell to routing and worker dispatch instead of mixed compatibility packaging.
- top-level legacy `run_task` invocation skeleton now also lives in `src/luxar/tools/run_task_compat.py`, including execution preparation, workflow-start event packaging, and stream-finalization glue.
- `run_task` execution-preparation, driver-request inference, explain/build/review message helpers, and auto-fix support logic now live in `src/luxar/tools/run_task_support.py`, so the shell continues to shrink toward pure legacy dispatch.
- workflow wrapper implementations for legacy `run_task` now live in `src/luxar/tools/run_task_workflows.py`, with `run_task.py` mostly forwarding into those implementations plus compatibility glue.
- legacy `run_task` intent dispatch is now table-driven inside `src/luxar/tools/run_task_workflows.py`, reducing the old compatibility shell further toward a mechanical router instead of an ad-hoc control plane.
- the remaining per-intent stream-dispatch glue for legacy `run_task` now lives in `src/luxar/tools/run_task_dispatch.py`, so `run_task.py` is closer to a pure export-and-compatibility shell with patchable dependency handles.
- the top-level legacy `run_task` / `run_task_stream` entrypoint implementations now live in `src/luxar/tools/run_task_entrypoints.py`, leaving `run_task.py` primarily as a public compatibility export plus patchable dependency handles.
- legacy `run_task` dependency bundle assembly now also lives in `src/luxar/tools/run_task_dependencies.py`, further shrinking `run_task.py` toward a pure export shell with intentionally preserved patch handles.
- the remaining legacy compatibility exports in `run_task.py` and `server/app.py` are now explicitly inventory-listed in retained-export module constants, and the temporary duplicate alias names for those inventories have already been removed.
- default `__all__` exports are now separated from those retained compatibility symbols, so legacy patch handles remain available as module attributes without still reading like the preferred public API surface.
- those retained-export inventories have now been narrowed again: `run_task.py` keeps only the legacy entrypoint surface itself in the retained list, and `server/app.py` keeps only the legacy HTTP opt-in environment switch there rather than every internal patch handle.
- default `ProjectManager` and `runtime_adapters` injection for legacy `run_task` now lives in `src/luxar/tools/run_task_dependencies.py`, so `run_task.py` no longer imports or exposes those lower-level worker dependencies itself.
- `src/luxar/server/app.py` no longer uses FastAPI's deprecated `@app.on_event("shutdown")` hook; conversation-store shutdown now runs through an app lifespan handler instead.
- vNext runtime/memory/workspace/skills/session-search route registration now lives in `src/luxar/server/vnext_surface.py`, so `app.py` no longer directly defines the bulk of the target 0.2.0 HTTP surface either.
- root/UI + config route registration now lives in `src/luxar/server/app_shell.py`, and project status inspection now lives in `src/luxar/server/project_status.py`, leaving `app.py` closer to a pure composition root with thin wrappers.
- tool call budget / timeout runtime support now lives in `src/luxar/server/tool_runtime.py`, with `app.py` only keeping thin compatibility wrappers for the existing patch points.
- post-run skill auto-extraction now lives in `src/luxar/server/skill_extraction.py`, so `app.py` no longer owns that bit of workflow-specific postprocessing logic either.
- The codebase still contains the old workers and, in several places, the new wrappers still depend on them.

So the user-facing architecture is cleaner than before, but the repository architecture is not yet fully cleaned up.

### 2. Legacy workspace skill storage is no longer the canonical root, but compatibility cleanup remains

`LUXAR0.2.0.md` explicitly says `workspace/skill_library/` is legacy and should be retired.

As of 2026-05-20:

- `workspace/skills/` exists and is the correct target
- the repository default tree no longer ships a committed `workspace/skill_library/`
- config/runtime canonical skill root resolution now points to `workspace/skills/`
- new protocol skill writes now go to `workspace/skills/protocols/`
- protocol skill reads now prefer `workspace/skills/protocols/`
- legacy protocol skills are auto-imported into the new root when discovered, and migrated metadata now rewrites legacy `path` fields to the new root

This means skill storage has converged onto the new 0.2.0 layout at the repository default and public/runtime levels, but compatibility readers still intentionally understand a legacy `workspace/skill_library/` source when one is encountered from older workspaces.

### 3. Server still carries non-target public surfaces

The 0.2.0 spec says the public API should converge on runtime/skills/memory/workspace/session-search.

`src/luxar/server/app.py` still also embeds broader legacy-era functionality such as:

- conversation orchestration tightly coupled to older internal systems

Important nuance:

- several non-target HTTP routes have now been removed from the default API registration path and only come back when `LUXAR_ENABLE_LEGACY_HTTP_SURFACE=1` is set, including the conversation/chat surface itself.

Some of these may remain useful, but they are not yet cleanly separated from the 0.2.0 target control plane.

## Verified Evidence

Verified on 2026-05-18:

- `py -m pytest tests/unit/test_vnext_runtime.py -q`
  Result: `11 passed`
- `py -m pytest tests/unit --collect-only -q`
  Result: `256 tests collected`
- `py -m pytest tests/unit/test_stm32_adapter.py -q`
  Result: `9 passed`
- `py -m pytest tests/unit/test_cli.py -q`
  Result: `20 passed`
- `py -m pytest tests/unit/test_vnext_architecture.py -q`
  Result: `3 passed`
- `py -m pytest tests/unit/test_server_app.py -q`
  Result: `12 passed`
- `py -m pytest tests/unit/test_skill_manager.py -q`
  Result: `6 passed`
- `py -m pytest tests/unit/test_skill_manager.py tests/unit/test_driver_generator.py tests/unit/test_server_app.py -q`
  Result: `22 passed`
- `py -m pytest tests/unit/test_config_manager.py tests/unit/test_asset_reuse.py -q`
  Result: `14 passed`
- `py -m pytest tests/unit/test_driver_generator.py tests/unit/test_forge_project.py -q`
  Result: `14 passed`
- `py -m pytest tests/unit/test_skill_manager.py -q`
  Result: `7 passed`
- `py -m pytest tests/unit/test_skill_manager.py tests/unit/test_driver_generator.py tests/unit/test_forge_project.py -q`
  Result: `21 passed`
- `py -m pytest tests/unit/test_memory_manager.py -q`
  Result: `3 passed`
- `py -m pytest tests/unit/test_vnext_runtime.py tests/unit/test_server_app.py -q`
  Result: `23 passed`

Attempted but not completed within the current terminal timeout:

- `py -m pytest tests/unit -q`
  Result: timed out before completion in this session, so full-suite pass is not claimed here.

## Recommended Next Migration Steps

Highest-priority cleanup to match `LUXAR0.2.0.md`:

1. Continue collapsing legacy internals into runtime workers, especially inside `src/luxar/server/app.py` and the remaining workflow-era orchestration helpers behind the new runtime adapter layer.
2. Physically retire `workspace/skill_library/` after the remaining compatibility readers and tests are migrated or removed.
3. Extend `workspace_probe()` from configuration evidence into hardware-backed evidence where supported.
4. Keep expanding architecture tests so prompt-first or workflow-first public surfaces cannot reappear through conversation plumbing.
5. Reduce `src/luxar/server/app.py` coupling by isolating strict 0.2.0 public endpoints from legacy chat/project plumbing.
6. Expand runtime-side evidence, escalation, and lesson-to-promotion behavior until the runtime loop does more than plan-and-dispatch.

## Bottom Line

LUXAR is no longer "pre-0.2.0". The repository already has the right skeleton and a working 0.2.0 public surface.

But it is also not yet "finished 0.2.0". The migration is incomplete because the old prompt-first/workflow-first internals still occupy too much of the control plane, and some 0.2.0 primitives are still placeholders or only baseline implementations.
