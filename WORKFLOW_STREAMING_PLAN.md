# Workflow Streaming Plan

## Goal

Keep the existing `run_task` routing and workflow behavior intact, but make long-running
project workflows observable in the chat stream. The agent should emit step-by-step progress
instead of staying silent until a large task finishes or times out.

## Current Problem

Today, the server treats `run_task` as a single blocking tool call:

1. The LLM emits one `tool_call` for `run_task`.
2. The server sends `tool_running`.
3. `run_task(...)` executes as a black box.
4. The server sends `tool_result` only after the whole workflow returns.

This causes:

- no intermediate user-visible progress
- poor failure localization
- 180 second timeout risk for valid long workflows
- repeated confusion between "tool is still working" and "tool is stuck"

Relevant files:

- `src/luxar/server/app.py`
- `src/luxar/tools/run_task.py`
- `src/luxar/tools/forge_project.py`
- `src/luxar/core/workflow_engine.py`
- `ui/public/index.html`

## Non-Goals

- Do not remove or break `run_task(...) -> dict`
- Do not rewrite `forge_project`, `debug_project`, or driver/review flows from scratch
- Do not change the existing routing semantics in `TaskRouter`
- Do not require frontend replacement; extend the current SSE handling

## Design Principles

1. Add streaming around existing workflows before restructuring internals.
2. Preserve the current return-style API for CLI, tests, and non-stream endpoints.
3. Reuse existing step semantics where they already exist, especially `WorkflowRunResult.steps`.
4. Make streaming additive: old callers continue to work.
5. Move from whole-task timeout to per-step visibility first; step-specific timeout tuning can come later.

## Proposed Architecture

### 1. Keep `run_task(...)` as-is

`run_task(...)` remains the stable synchronous orchestration entrypoint and continues to return:

- `mode`
- `execution_plan`
- `message`
- `workflow`
- `build_result`
- `report`

This protects existing tests and integrations.

### 2. Add `run_task_stream(...)`

Add a new generator-style entrypoint in `src/luxar/tools/run_task.py`:

```python
def run_task_stream(...):
    yield {"event": "workflow_started", ...}
    yield {"event": "workflow_step_started", ...}
    yield {"event": "workflow_step_finished", ...}
    yield {"event": "workflow_finished", ...}
```

This function should:

- reuse the same `TaskRouter`
- produce the same execution plan as `run_task`
- call the same underlying workflows
- emit progress before and after each stage
- still produce a final summary payload compatible with current UI/result cards

### 3. Introduce a small workflow event schema

Use lightweight dict events first; no large model refactor is required initially.

Minimum event types:

- `workflow_started`
- `workflow_step_started`
- `workflow_step_finished`
- `workflow_warning`
- `workflow_failed`
- `workflow_finished`

Recommended common fields:

- `workflow`
- `step`
- `message`
- `status`
- `payload`
- `ts`

Example:

```json
{
  "event": "workflow_step_started",
  "workflow": "forge",
  "step": "review",
  "message": "Starting application review"
}
```

### 4. Stream from `server/app.py` without changing other tools

Special-case `run_task` in the streaming agent loop:

- keep normal `tool_call` and `tool_running`
- if the tool is `run_task`, call `run_task_stream(...)`
- forward workflow events to SSE as they arrive
- emit the final `tool_result` only after the workflow completes

This preserves the existing tool protocol while making only `run_task` observable.

### 5. Extend the UI with a workflow timeline

Add handling in `ui/public/index.html` for:

- `workflow_started`
- `workflow_step_started`
- `workflow_step_finished`
- `workflow_warning`
- `workflow_failed`
- `workflow_finished`

UI behavior:

- append compact timeline rows into the active assistant bubble
- preserve existing markdown/token rendering
- do not replace existing result cards
- keep current tool events visible

## Phase 1: Minimal-Change Implementation

### `forge_project`

`run_forge_project(...)` already has explicit stage boundaries. Stream around these stages:

- `parse_docs`
- `plan`
- `resolve_drivers`
- `reuse_drivers`
- `generate_drivers`
- `assemble`
- `generate_app`
- `review`
- `fix`
- `build`
- `flash`
- `monitor`

Implementation approach:

- before each major block: emit `workflow_step_started`
- after each major block: emit `workflow_step_finished`
- on early return/failure: emit `workflow_failed`

This can be done by extracting a sibling function such as:

```python
def run_forge_project_stream(...):
    ...
```

The existing `run_forge_project(...)` stays unchanged initially.

### `debug_project`

`WorkflowEngine.run_debug_workflow(...)` already converts `DebugLoopResult` into structured steps.
First phase options:

1. low-risk wrapper mode:
   emit coarse steps before/after the whole debug workflow, then unfold the returned steps
2. better progressive mode:
   add a streaming variant under `DebugLoop` / `WorkflowEngine`

Recommendation:

- start with coarse wrapper mode for speed and safety
- promote to true internal step streaming later

### `review_or_fix`

This path is naturally streamable:

- `review`
- optional `autofix`
- `rereview`

Unlike forge/debug, it may not need a dedicated stream helper; it can be streamed directly inside `run_task_stream(...)`.

## Phase 2: Shared Helpers

After Phase 1 works, add a small helper module, for example:

- `src/luxar/core/workflow_events.py`

Suggested helpers:

- `emit_workflow_started(...)`
- `emit_step_started(...)`
- `emit_step_finished(...)`
- `emit_workflow_failed(...)`
- `emit_workflow_finished(...)`

This avoids duplicating event dict construction across workflows.

## Phase 3: Timeout Strategy

Once step streaming exists, refine timeout behavior:

- keep a hard upper bound for total request lifetime
- add per-step timing metadata
- only fail a step when that step exceeds its limit
- avoid treating the full workflow as one 180 second opaque block

Possible approach:

- keep current outer SSE request alive
- move timeout enforcement to streamed sub-steps
- optionally emit heartbeat/warning events for long but healthy steps

## Concrete File Changes

### `src/luxar/tools/run_task.py`

Add:

- `run_task_stream(...)`
- helper functions for streaming `review_or_fix`
- helper functions for dispatching to stream or sync workflow variants

Keep:

- `run_task(...)` unchanged for current callers

### `src/luxar/tools/forge_project.py`

Add:

- `run_forge_project_stream(...)`

Keep:

- `run_forge_project(...)`

### `src/luxar/core/workflow_engine.py`

Possible additions:

- `run_debug_workflow_stream(...)` later

Initial phase can defer this if `debug` is wrapped coarsely outside.

### `src/luxar/server/app.py`

Change the streaming tool execution path:

- detect `run_task`
- iterate the event generator
- map workflow events to SSE
- still append a final tool result into conversation state

### `ui/public/index.html`

Add timeline rendering for workflow SSE events.

## Backward Compatibility

This plan preserves:

- all current non-stream behavior
- all current CLI usage
- all existing workflow result objects
- the current `WorkflowRunResult` model

Only the streaming chat path gets new intermediate events.

## Testing Plan

### Unit tests

Add tests for:

- `run_task_stream(...)` emits ordered events
- forge streaming reports step start/finish
- review/fix streaming reports re-review after autofix
- stream path in `server/app.py` forwards workflow events
- final `tool_result` still appears after workflow completion

### UI tests

Add string-presence tests for new SSE event handlers in `ui/public/index.html`.

### Regression tests

Ensure existing tests for:

- `run_task(...)`
- project creation
- timeout errors for other tools
- non-stream conversations

continue to pass unchanged.

## Recommended Delivery Order

1. Add workflow SSE event names and UI handlers.
2. Implement `run_task_stream(...)` with `review_or_fix` path first.
3. Add `run_forge_project_stream(...)`.
4. Hook `run_task` streaming into `server/app.py`.
5. Add coarse `debug` streaming.
6. Refine timeouts and heartbeat behavior.

## First Safe Milestone

The first milestone should already solve the user-visible problem if it delivers:

- streamed `run_task` events for `forge_project`
- step timeline in the chat UI
- final results preserved
- no behavior change for non-stream callers

That gets LUXAR out of the "silent 180 seconds" failure mode without rewriting the current engine.
