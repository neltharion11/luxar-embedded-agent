# LUXAR LangGraph

A clean, enterprise-shaped reconstruction of LUXAR using explicit domain models, Ports and Adapters, structured execution evidence, and LangGraph orchestration.

## Current milestone

The evidence-driven Agent workflow now supports both deterministic Fakes and
production DeepSeek model Adapters:

```text
natural-language task
  → validated FirmwareRequirement
  → validated ExecutionPlan
  → BuildEvidence + BuildDiagnostic
  → completed
    or timeout retry
    or structured RepairPlan → restricted workspace update → rebuild
    or terminal failure
```

LangGraph owns State transitions, conditional routing, the bounded repair loop, and streaming. Domain models own validation. Ports describe external capabilities. Runtime Context can inject either deterministic Fakes or the production DeepSeek requirement, planning, and repair Adapters without changing the Graph topology.

The default suite uses no real model call, filesystem write, or `idf.py`. A centralized application Runner converts model-side capability failures into sanitized failed State values while preserving the latest requirement, plan, build evidence, and diagnostics.

## Production entry chain

```text
build_deepseek_runtime_context(...)
  → RuntimeContext with one shared DeepSeek client
  → run_workflow(initial_state=..., context=...)
  → compiled LangGraph
  → completed, clarification, or sanitized failed WorkflowState
```

`build_deepseek_runtime_context(...)` selects concrete external capabilities.
`run_workflow(...)` is the production execution boundary and contains the one
application-level `CapabilityError` handler. Business nodes remain provider
independent.

## Core topology

```text
START → analyze_requirement
  ├─ incomplete → request_clarification → END
  └─ complete → create_plan → build_project
                                ├─ success → completed → END
                                ├─ timeout → build_project
                                ├─ source/linker → repair_project → build_project
                                └─ environment/unknown/limit → failed → END
```

## Next production slices

1. `LocalWorkspaceAdapter` with resolved-path containment, file allowlists, and size limits.
2. ESP-IDF CLI Adapter with GCC/CMake/linker diagnostic parsing into `BuildDiagnostic`.
3. A real application entrypoint that composes those engineering Adapters with the completed DeepSeek model slice.

## Development environment

```bat
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pip install -e ".[dev]"
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
```

## Optional real DeepSeek smoke

The default command skips the real API smoke test. To explicitly spend one
minimal DeepSeek request, provide `DEEPSEEK_API_KEY`, set
`LUXAR_RUN_DEEPSEEK_SMOKE=1`, and run:

```bat
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/smoke/test_deepseek_requirement_parser.py
```

Never place the API key in source code, Markdown, State, prompts, or Git.
