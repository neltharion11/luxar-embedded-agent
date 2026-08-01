# LUXAR LangGraph

A clean, enterprise-shaped reconstruction of LUXAR using explicit domain models, Ports and Adapters, structured execution evidence, and LangGraph orchestration.

## Current milestone

The first fake-backed, evidence-driven Agent workflow is executable:

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

LangGraph owns State transitions, conditional routing, the bounded repair loop, and streaming. Domain models own validation. Ports describe external capabilities. Runtime Context injects Fake implementations today and will inject DeepSeek, local workspace, and ESP-IDF implementations without changing the Graph topology.

The current suite uses no real model call, filesystem write, or `idf.py`; its purpose is to prove the business topology and dependency boundaries deterministically before production Adapters are added.

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

1. `DeepSeekRequirementParser`, `DeepSeekPlanner`, and `DeepSeekRepairPlanner` with mocked API-response tests.
2. `LocalWorkspaceAdapter` with resolved-path containment, file allowlists, and size limits.
3. ESP-IDF CLI Adapter with GCC/CMake/linker diagnostic parsing into `BuildDiagnostic`.

## Development environment

```bat
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pip install -e ".[dev]"
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
```
