# LUXAR Enterprise Learning Progress

Updated: 2026-08-01

## Current milestone

The learner has completed and compiled the seven-node evidence-driven repair Graph. The Fake-backed vertical slice now runs from natural-language input through requirement parsing, planning, build evidence, bounded repair/retry, and an explicit terminal state.

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

## Authorship contract

The learner writes learning-critical Domain models, Ports, Adapter behavior, State, Runtime Context, nodes, routing decisions, and Graph builders. Codex maintains scaffolding, test fixtures, explanatory documentation, progress records, and commit summaries.
