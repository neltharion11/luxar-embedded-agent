# LUXAR LangGraph

A clean, enterprise-shaped reconstruction of LUXAR using explicit domain models, Ports and Adapters, structured execution evidence, and LangGraph orchestration.

## Learning notes

Start with the Chinese [LUXAR Agent review guide](docs/learning/00-LUXAR-Agent-复习总览.md). It consolidates English-to-Chinese terminology, Python syntax used by this project, the layered architecture, end-to-end workflow paths, testing levels, and Agent safety rules. The numbered `docs/learning/01` through `08` files remain focused deep dives.

## Current milestone

The evidence-driven Agent workflow now supports deterministic Fakes,
production DeepSeek model Adapters, and a path-contained local filesystem
Workspace Adapter:

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

The default suite uses no real model call, real project write, or `idf.py`.
Filesystem tests write only pytest temporary directories. A centralized
application Runner converts model-side and workspace-side capability failures
into sanitized failed State values while preserving the latest requirement,
plan, build evidence, and diagnostics.

## Production entry chain

```text
build_deepseek_runtime_context(...)
  → RuntimeContext with one shared DeepSeek client
  → run_workflow(initial_state=..., context=...)
  → compiled LangGraph
  → completed, clarification, or sanitized failed WorkflowState
```

`build_deepseek_runtime_context(...)` selects concrete external capabilities.
`run_workflow(...)` is the production execution boundary and contains one
application-level handler for `CapabilityError` and `WorkspaceError`.
Business nodes remain provider and filesystem-implementation independent.

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

1. ESP-IDF CLI Adapter with environment/dependency preflight, default-disabled dependency downloads, and diagnostic parsing into `BuildDiagnostic`.
2. A real application entrypoint that composes the Local Workspace and ESP-IDF engineering Adapters with the completed DeepSeek model slice.

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
