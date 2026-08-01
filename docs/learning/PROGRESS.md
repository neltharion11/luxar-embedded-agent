# LUXAR Enterprise Learning Progress

Updated: 2026-08-01

## Current milestone

The formal Python package baseline and the first Domain object, `FirmwareRequirement`, are complete.

## Verified work

- `src/` package imports through an editable installation.
- `FirmwareRequirement` validates the ESP-IDF platform boundary.
- Completeness is derived from explicit missing fields.
- Pydantic domain validation remains independent of LangGraph.
- Domain suite: `4 passed`.

## Next technical slice

The project now distinguishes an intended action (`ExecutionPlan`), an observed tool fact (`BuildEvidence`), and a normalized failure (`WorkflowError`). The complete Domain suite passes 15 tests.

The application-owned Ports for requirement parsing, planning, and ESP-IDF building are complete. Core layers contain no DeepSeek, OpenAI-compatible client, subprocess, LangGraph, or API-key dependencies.

Deterministic Fake Adapters now satisfy the three Ports, record calls, and provide controlled requirement, plan, and build evidence. Adapter suite: 4 passed; full suite: 19 passed.

`WorkflowState` and frozen `RuntimeContext` are complete. The State holds business progress; the Context injects Parser, Planner, ESP-IDF, and project path without provider leakage. Full suite: 20 passed.

Next: implement LangGraph nodes that use `Runtime[RuntimeContext]` to call Ports and return minimal State updates.

## Authorship contract

The learner writes learning-critical Domain models, Ports, Adapter behavior, State, Runtime Context, nodes, routing decisions, and Graph builders. Codex maintains scaffolding, test fixtures, explanatory documentation, progress records, and commit summaries.
