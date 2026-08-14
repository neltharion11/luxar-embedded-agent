# Evidence-Driven Repair Graph

## Executable topology

```text
START → analyze_requirement
  ├─ incomplete → request_clarification → END
  └─ complete → create_plan → build_project
                                ├─ success → completed → END
                                ├─ timeout within budget → build_project
                                ├─ source/linker within budget
                                │      → repair_project → build_project
                                └─ environment/unknown/exhausted → failed → END
```

Ordinary edges represent unconditional business progress. Conditional edges call pure routing functions with the latest `WorkflowState`. The repair-to-build edge creates the feedback loop; `attempts` is incremented only by `build_project`, so `max_attempts` proves finite termination.

## Compile time and invocation time

`build_graph()` registers seven business nodes and compiles their topology. It does not construct DeepSeek clients, filesystem implementations, ESP-IDF tools, Fakes, credentials, or a project path.

At invocation time, `RuntimeContext` supplies objects satisfying `RequirementParser`, `Planner`, `RepairPlanner`, `EspIdfPort`, and `WorkspacePort`. The same compiled topology can therefore run with deterministic Fakes or later production Adapters.

## Repair data path

`build_project` stores tool-owned `BuildEvidence`, including structured `BuildDiagnostic` values. For source/linker errors, `repair_project` reads validated `ProjectFile` snapshots, asks `RepairPlanner` for a validated `RepairPlan`, and delegates complete-file application to `WorkspacePort`. The next build replaces the old evidence with a new tool fact; a repair proposal never proves success by itself.

## Verified behavior

The integration suite proves clarification without planning, immediate/second-attempt completion, timeout retry without repair, environment failure without repair, and source failure followed by one repair and a successful rebuild. Streaming reports the repair path in this exact order:

```text
analyze_requirement
create_plan
build_project
repair_project
build_project
completed
```

Final verification on 2026-08-01: 65 tests passed. Searches found no old `luxar_learning` import and no DeepSeek/OpenAI client, `subprocess`, `idf.py`, or LangChain implementation in Domain, Application, or Ports.
