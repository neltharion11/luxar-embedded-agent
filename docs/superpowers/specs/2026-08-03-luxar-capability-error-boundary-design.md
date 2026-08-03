# LUXAR Capability Error Boundary Design

**Date:** 2026-08-03

**Status:** Approved in teaching; pending written-spec review

**Repository:** `C:\tmp\luxar-langgraph`

## Goal

Convert every model-side `CapabilityError` into one sanitized `WorkflowError` and an explicit failed `WorkflowState`, while preserving the last successfully emitted State and keeping the existing seven-node LangGraph topology unchanged.

## Selected Boundary

Add one application-level workflow Runner around the compiled Graph. Production callers use the Runner instead of calling `graph.invoke()` directly.

The Runner consumes `graph.stream(..., stream_mode="values")` and retains the most recent complete State snapshot. It contains one `except CapabilityError` block. On success it returns the final snapshot. On failure it combines the last snapshot with a normalized `WorkflowError` and the existing `failed()` node update.

The business nodes remain focused on their normal work and contain no duplicated provider exception handling. The Graph keeps its current nodes, ordinary edges, conditional edges, and topology test.

## Why `Command(goto="failed")` Is Not Used

LangGraph `Command` adds dynamic routing but does not cancel existing static or conditional routing. Returning a failure `Command` from the current nodes could therefore schedule both the existing destination and `failed`. The application Runner avoids mixed routing semantics.

## State Preservation

Values streaming emits the full State after each successfully completed Graph step. If a later model call fails, the Runner still owns the latest successful snapshot.

This preserves:

- the original task text;
- a parsed requirement when planning fails;
- the requirement, execution plan, build attempts, last `BuildEvidence`, diagnostics, and earlier repair history when repair planning fails.

No raw SDK exception, API key, response body, or provider object enters State.

## Stage Selection

This boundary handles model Adapter failures only. The failed stage is derived from the last validated State:

- no `requirement`: `requirement_analysis`;
- `requirement` exists but no `plan`: `planning`;
- both `requirement` and `plan` exist: `repair`.

ESP-IDF execution and Workspace failures remain outside this model-capability slice and will receive separate evidence/error policies when their real Adapters are implemented.

## Error Mapping

`WorkflowError` gains the provider-independent stage `repair` and capability categories needed for accurate operator feedback.

| Capability category | Workflow category |
|---|---|
| `authentication` | `authentication` |
| `timeout` | `timeout` |
| `rate_limit` | `rate_limit` |
| `service` | `service` |
| `empty_response` | `model_output` |
| `invalid_json` | `model_output` |
| `invalid_schema` | `model_output` |

The retryability flag is preserved. `WorkflowError.message` and `user_suggestion` are selected from fixed application-owned text for the normalized category; `CapabilityError.message`, raw provider details, and sensitive markers are never copied into State. This makes the Runner a second sanitization boundary even if a future Adapter constructs an unsafe exception message.

## Optional Real Smoke Test

The real requirement-parser smoke test runs only when both `DEEPSEEK_API_KEY` is present and `LUXAR_RUN_DEEPSEEK_SMOKE=1`. Default pytest collects the test but skips it without making a network request. The smoke sends one minimal ESP32 requirement, validates the result as `FirmwareRequirement`, and prints neither the key nor the raw model response.

## Testing

Offline tests must prove:

- successful Runner execution returns the ordinary final State;
- authentication, timeout, rate-limit, service, and model-output failures return `status="failed"`;
- the failure contains the correct stage, normalized category, retryability, and fixed suggestion;
- planning failures preserve the parsed requirement;
- repair failures preserve the latest `BuildEvidence` and diagnostics;
- exception messages do not expose supplied sensitive markers;
- the existing Graph topology test remains unchanged;
- the smoke test skips unless both opt-in conditions are true.

## Success Criteria

1. One application-level `except CapabilityError` exists.
2. No model business node contains provider or capability `try/except` code.
3. Failed model calls return a structured failed State instead of escaping to the production caller.
4. The last successful workflow data remains available for diagnosis.
5. Default tests remain offline and the existing Graph topology remains unchanged.
