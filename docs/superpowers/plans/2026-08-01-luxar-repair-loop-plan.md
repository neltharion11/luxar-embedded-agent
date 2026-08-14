# LUXAR Evidence-Driven Repair Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not delegate the learner's core coding exercises to subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the current fake-backed LUXAR vertical slice so source and linker failures are repaired through a structured LLM-shaped plan and a restricted workspace capability before rebuilding.

**Architecture:** Pydantic Domain models carry compiler diagnostics, project snapshots, and complete-file replacements. A single LangGraph `repair_project` node orchestrates a `RepairPlanner` Port and a `WorkspacePort`; build routing distinguishes success, repair, timeout retry, and terminal failure. Fake Adapters prove the topology before DeepSeek and real filesystem/ESP-IDF implementations are added.

**Tech Stack:** Python 3.12, LangGraph `>=1.2,<1.3`, Pydantic `>=2,<3`, pytest `>=8,<9`, `typing.Protocol`, `pathlib.Path`.

## Global Constraints

- Repository: `C:\tmp\luxar-langgraph`.
- Verification command: `C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider`.
- The learner writes learning-critical Domain models, Ports, Fake behavior, State/Context changes, nodes, routing, and Graph builder.
- Codex writes and maintains all tests, README content, architecture notes, progress records, and other Markdown.
- Present the correct current-step implementation and its purpose before the learner types it; do not use prediction quizzes or deliberately broken teaching code.
- Domain imports neither LangGraph nor provider/tool implementations.
- Application imports Domain and Ports, never concrete Adapters.
- DeepSeek proposes `RepairPlan`; only `WorkspacePort` applies files; only `EspIdfPort` produces `BuildEvidence`.
- Automatic writes are restricted to validated paths beneath the configured project root.
- Build attempts have an explicit maximum; source/linker failures must change files before rebuilding; timeout is the only direct no-change retry.
- The first slice uses Fakes and does not call the real DeepSeek API, filesystem writer, or `idf.py`.

## Current Checkpoint

- Tasks 1–6 of `2026-08-01-luxar-enterprise-foundation-plan.md` are committed through `d5dd6fe`.
- The learner has completed the six original nodes and the requirement router.
- Codex node and requirement-route tests pass; the working tree contains these uncommitted files because the environment denied `.git/index.lock` creation.
- The obsolete build-route instructions in original Task 8 and the six-node topology in original Task 9 must not be implemented.

## File Map

| Path | Responsibility |
|---|---|
| `src/luxar/domain/evidence.py` | `BuildDiagnostic` and `BuildEvidence` |
| `src/luxar/domain/repairs.py` | `ProjectFile`, `FileReplacement`, `RepairPlan` and path invariants |
| `src/luxar/ports/repair_planner.py` | Repair proposal capability |
| `src/luxar/ports/workspace.py` | Restricted project file capability |
| `src/luxar/adapters/fake_repair_planner.py` | Deterministic repair proposal and call recording |
| `src/luxar/adapters/fake_workspace.py` | Deterministic project snapshot/application and call recording |
| `src/luxar/application/state.py` | Repair progress in persistent business State |
| `src/luxar/application/context.py` | Runtime injection of repair capabilities |
| `src/luxar/application/nodes.py` | `repair_project` orchestration |
| `src/luxar/application/routing.py` | Evidence-driven build destination |
| `src/luxar/application/graph.py` | Seven-node business topology |
| `tests/` | Domain, Fake, node, route, Graph, and vertical integration proof |

---

### Task 1: Add Structured Build Diagnostics

**Files:**

- Learner modifies: `src/luxar/domain/evidence.py`
- Codex modifies: `tests/domain/test_evidence.py`
- Codex updates: `docs/learning/01-domain-models.md`

**Interfaces:**

- Produces `BuildDiagnostic(BaseModel)` with `file`, `line`, `column`, `severity`, `code`, and `message`.
- Adds `BuildEvidence.diagnostics: list[BuildDiagnostic] = Field(default_factory=list)`.

- [ ] **Step 1: Teach compiler text versus structured evidence**

Explain why `stderr_summary` remains useful but cannot reliably drive file selection, and why DeepSeek cannot create or alter build facts.

- [ ] **Step 2: Learner implements the diagnostic model and evidence field**

```python
class BuildDiagnostic(BaseModel):
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    severity: Literal["warning", "error"]
    code: str | None = None
    message: str = Field(min_length=1)


class BuildEvidence(BaseModel):
    # Existing fields remain unchanged.
    diagnostics: list[BuildDiagnostic] = Field(default_factory=list)
```

- [ ] **Step 3: Codex adds invariant tests**

Test file/line/column preservation, rejection of zero line/column, rejection of empty messages, and independent diagnostics list defaults.

- [ ] **Step 4: Verify**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest tests/domain/test_evidence.py -v -p no:cacheprovider
```

- [ ] **Step 5: Save checkpoint**

```powershell
git add src/luxar/domain/evidence.py tests/domain/test_evidence.py docs/learning/01-domain-models.md
git commit -m "feat: preserve structured build diagnostics"
```

---

### Task 2: Model Safe Complete-File Repairs

**Files:**

- Learner creates: `src/luxar/domain/repairs.py`
- Codex creates: `tests/domain/test_repairs.py`
- Codex creates: `docs/learning/04-repair-domain.md`

**Interfaces:**

- Produces `ProjectFile(path: str, content: str)`.
- Produces `FileReplacement(path: str, content: str)`.
- Produces `RepairPlan(diagnosis: str, replacements: list[FileReplacement])`.
- Both path-bearing models normalize `\` to `/` and reject empty, absolute, drive-qualified, and parent-traversal paths.
- `RepairPlan` requires at least one replacement and unique target paths.

- [ ] **Step 1: Teach model output versus filesystem authority**

Explain that Pydantic validates the proposal, while the Workspace Adapter performs a second resolved-path containment check before any write.

- [ ] **Step 2: Learner implements safe path validation and repair models**

Use `field_validator` for path normalization and `model_validator(mode="after")` for duplicate replacement detection. Use both `PurePosixPath` and `PureWindowsPath` to reject cross-platform absolute paths.

- [ ] **Step 3: Codex adds complete domain tests**

Test valid nested paths, backslash normalization, empty path, `/absolute`, `C:\absolute`, `../escape`, empty replacements, and duplicate targets.

- [ ] **Step 4: Verify**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest tests/domain/test_repairs.py -v -p no:cacheprovider
```

- [ ] **Step 5: Save checkpoint**

```powershell
git add src/luxar/domain/repairs.py tests/domain/test_repairs.py docs/learning/04-repair-domain.md
git commit -m "feat: model restricted complete-file repairs"
```

---

### Task 3: Add Repair and Workspace Capability Boundaries

**Files:**

- Learner creates: `src/luxar/ports/repair_planner.py`
- Learner creates: `src/luxar/ports/workspace.py`
- Learner creates: `src/luxar/adapters/fake_repair_planner.py`
- Learner creates: `src/luxar/adapters/fake_workspace.py`
- Codex modifies: `tests/adapters/test_fake_contracts.py`
- Codex updates: `docs/learning/02-ports-and-adapters.md`

**Interfaces:**

```python
class RepairPlanner(Protocol):
    def create_repair(
        self,
        requirement: FirmwareRequirement,
        plan: ExecutionPlan,
        evidence: BuildEvidence,
        files: list[ProjectFile],
    ) -> RepairPlan: ...


class WorkspacePort(Protocol):
    def read_project_files(self, project_path: Path) -> list[ProjectFile]: ...
    def apply_repair(self, project_path: Path, repair: RepairPlan) -> list[str]: ...
```

- [ ] **Step 1: Teach why two Ports remain one Graph node**

Explain that `RepairPlanner` proposes and `WorkspacePort` applies; `repair_project` is the business step that coordinates both capabilities.

- [ ] **Step 2: Learner implements both Protocols**

Keep prompts, API clients, file APIs, LangGraph types, routing, and credentials out of the Port modules.

- [ ] **Step 3: Learner implements deterministic Fakes**

`FakeRepairPlanner` returns a constructor-supplied `RepairPlan` and records all four inputs. `FakeWorkspace` returns constructor-supplied `ProjectFile` values, records read/apply calls, and returns the replacement paths without touching disk.

- [ ] **Step 4: Codex adds contract tests**

Verify configured results, exact call records, call ordering observable through each Fake, and no implicit filesystem writes.

- [ ] **Step 5: Verify**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest tests/adapters -v -p no:cacheprovider
```

- [ ] **Step 6: Save checkpoint**

```powershell
git add src/luxar/ports src/luxar/adapters tests/adapters docs/learning/02-ports-and-adapters.md
git commit -m "feat: define repair and workspace ports"
```

---

### Task 4: Inject and Execute the Repair Step

**Files:**

- Learner modifies: `src/luxar/application/state.py`
- Learner modifies: `src/luxar/application/context.py`
- Learner modifies: `src/luxar/application/nodes.py`
- Codex modifies: `tests/application/test_state_boundaries.py`
- Codex modifies: `tests/application/test_nodes.py`
- Codex updates: `docs/learning/03-state-and-runtime.md`

**Interfaces:**

- State adds `repair_plan: RepairPlan`, `changed_files: list[str]`, and status `"repaired"`.
- Runtime Context adds `repair_planner: RepairPlanner` and `workspace: WorkspacePort`.
- Produces `repair_project(state: WorkflowState, runtime: Runtime[RuntimeContext]) -> dict[str, object]`.

- [ ] **Step 1: Teach the complete call chain**

Trace each value from State and Context through `read_project_files`, `create_repair`, `apply_repair`, and the minimal State update.

- [ ] **Step 2: Learner extends State and Context**

Keep Adapter instances in Context and only serializable business results in State.

- [ ] **Step 3: Learner implements `repair_project`**

The node reads files, passes requirement/plan/evidence/files to the planner, applies the returned plan, and returns `repair_plan`, `changed_files`, `status="repaired"`, and an appended trace. It does not increment build attempts.

- [ ] **Step 4: Codex adds direct node tests**

Verify exact arguments, returned updates, trace order, unchanged attempts, and preservation of the incoming evidence object.

- [ ] **Step 5: Verify**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest tests/application/test_state_boundaries.py tests/application/test_nodes.py -v -p no:cacheprovider
```

- [ ] **Step 6: Save checkpoint**

```powershell
git add src/luxar/application tests/application docs/learning/03-state-and-runtime.md
git commit -m "feat: apply structured repairs through runtime ports"
```

---

### Task 5: Route Build Evidence Through Repair or Retry

**Files:**

- Learner modifies: `src/luxar/application/routing.py`
- Codex modifies: `tests/application/test_routing.py`

**Interfaces:**

- Produces `route_after_build(state: WorkflowState) -> Literal["completed", "repair_project", "build_project", "failed"]`.

- [ ] **Step 1: Teach the route table and finite-loop proof**

Decision order is success, exhausted budget, source/linker repair, timeout retry, and terminal environment/unknown failure. `attempts` counts builds, not repair plans.

- [ ] **Step 2: Learner implements the pure route**

```python
def route_after_build(
    state: WorkflowState,
) -> Literal["completed", "repair_project", "build_project", "failed"]:
    evidence = state["build_evidence"]
    attempts = state.get("attempts", 0)
    max_attempts = state.get("max_attempts", 1)

    if evidence.success:
        return "completed"
    if attempts >= max_attempts:
        return "failed"
    if evidence.error_category in {"source", "linker"}:
        return "repair_project"
    if evidence.error_category == "timeout":
        return "build_project"
    return "failed"
```

- [ ] **Step 3: Codex completes route-table tests**

Cover success at the limit, source/linker below the limit, timeout below the limit, each failure category at the limit, environment, unknown, and missing category.

- [ ] **Step 4: Verify**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest tests/application/test_routing.py -v -p no:cacheprovider
```

- [ ] **Step 5: Save checkpoint**

```powershell
git add src/luxar/application/routing.py tests/application/test_routing.py
git commit -m "feat: route build evidence through bounded repair"
```

---

### Task 6: Compile and Verify the Seven-Node Graph

**Files:**

- Learner creates: `src/luxar/application/graph.py`
- Codex creates: `tests/application/test_graph.py`
- Codex creates: `tests/integration/test_fake_vertical_slice.py`
- Codex updates: `README.md`
- Codex creates: `docs/learning/05-repair-graph.md`
- Codex updates: `docs/learning/PROGRESS.md`

**Interfaces:**

- Produces `build_graph() -> CompiledStateGraph` with `context_schema=RuntimeContext`.
- Registers `analyze_requirement`, `create_plan`, `build_project`, `repair_project`, `request_clarification`, `completed`, and `failed`.

- [ ] **Step 1: Teach compile-time topology versus invocation-time dependencies**

Explain ordinary edges, the requirement conditional edge, the build conditional edge, the repair-to-build loop, explicit terminal edges, and why Graph construction never instantiates Fakes or production Adapters.

- [ ] **Step 2: Learner implements the Graph builder**

Add `START → analyze_requirement`, requirement destinations, `create_plan → build_project`, all four build destinations, `repair_project → build_project`, and each terminal node to `END`.

- [ ] **Step 3: Codex adds Graph and vertical integration tests**

Verify clarification, immediate success, timeout then success, source failure then one repair then success, terminal environment failure, exhausted-budget failure, exact Adapter call counts, final evidence, changed files, trace, and streaming node order.

- [ ] **Step 4: Run the full gate**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
rg -n "from luxar_learning|import luxar_learning" src tests
rg -n "openai|deepseek|subprocess|idf.py|langchain" src/luxar/domain src/luxar/application src/luxar/ports
```

Expected: all tests pass; no learning-repo imports; no provider or tool implementations leak into core layers.

- [ ] **Step 5: Codex records the architecture checkpoint**

Document the full request-to-diagnostic-to-repair-to-evidence path and identify `DeepSeekRepairPlanner`, `LocalWorkspaceAdapter`, and the real ESP-IDF diagnostic parser as the next production slices.

- [ ] **Step 6: Save the vertical-slice checkpoint**

```powershell
git add .
git commit -m "feat: complete evidence-driven repair workflow"
```

## Final Learning Gate

Before real DeepSeek and ESP-IDF integration, the repository must prove:

1. Compiler diagnostics are structured tool facts, not model claims.
2. Repair proposals cannot address absolute or parent-traversal paths.
3. The Graph uses one `repair_project` node backed by two independently replaceable Ports.
4. Source/linker failures modify files before rebuilding; timeout alone may directly retry.
5. Environment and unknown errors terminate without wasting attempts.
6. Every loop stops at `max_attempts` and preserves the final `BuildEvidence`.
7. The learner can trace State values, Runtime dependencies, Port calls, Adapter behavior, and route decisions through the complete repair cycle.

