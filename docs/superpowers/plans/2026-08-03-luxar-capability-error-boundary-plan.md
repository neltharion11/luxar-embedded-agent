# LUXAR Capability Error Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not delegate the learner's core coding exercises to subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert model-side `CapabilityError` values at one application boundary into sanitized `WorkflowError` values and explicit failed workflow State without changing the seven-node Graph topology.

**Architecture:** A new application Runner consumes full State snapshots from the compiled Graph and stores the latest successful snapshot. One `except CapabilityError` converts the failure through a pure mapping function, merges the existing `failed()` node update, and returns a failed `WorkflowState`; the three business nodes remain unchanged.

**Tech Stack:** Python 3.12, LangGraph `>=1.2,<1.3`, Pydantic `>=2,<3`, pytest `>=8,<9`.

## Global Constraints

- Repository: `C:\tmp\luxar-langgraph`.
- Tests: `C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider`.
- Only one `except CapabilityError` may exist in the application workflow path.
- The existing seven-node Graph topology and `tests/application/test_graph.py` remain unchanged.
- `CapabilityError.message`, SDK exceptions, raw responses, and API secrets never enter State.
- Default tests make no network calls.
- Real smoke requires both `DEEPSEEK_API_KEY` and `LUXAR_RUN_DEEPSEEK_SMOKE=1`.
- The learner writes the Domain extension, error mapping, and Runner. Codex writes tests, smoke coverage, Markdown, progress records, and commit summaries.
- Teach the complete call chain and new syntax before learner implementation.

---

### Task 1: Extend the Workflow Error Vocabulary

**Files:**

- Learner modifies: `src/luxar/domain/errors.py`
- Codex modifies: `tests/domain/test_errors.py`

**Interfaces:**

- `WorkflowError.stage` additionally accepts `"repair"`.
- `WorkflowError.category` additionally accepts `"authentication"`, `"rate_limit"`, and `"service"`.
- Existing stages and categories remain valid.

- [x] **Step 1: Codex adds failing vocabulary tests**

Add parameterized construction tests equivalent to:

```python
@pytest.mark.parametrize(
    ("stage", "category"),
    [
        ("repair", "model_output"),
        ("requirement_analysis", "authentication"),
        ("planning", "rate_limit"),
        ("repair", "service"),
    ],
)
def test_workflow_error_accepts_model_capability_failures(
    stage: str,
    category: str,
) -> None:
    error = WorkflowError(
        stage=stage,
        category=category,
        message="safe message",
        retryable=False,
    )

    assert error.stage == stage
    assert error.category == category
```

- [x] **Step 2: Run the focused test and verify RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/domain/test_errors.py
```

Expected: the new cases fail with Pydantic literal-validation errors because the vocabulary is not yet extended.

- [x] **Step 3: Teach the Domain change**

Explain that `Literal` is a runtime vocabulary boundary through Pydantic, not merely an editor hint here. The new words describe provider-independent operational failures and do not mention DeepSeek.

- [x] **Step 4: Learner extends the two `Literal` lists**

The resulting fields are:

```python
stage: Literal[
    "requirement_analysis",
    "planning",
    "project_creation",
    "build",
    "repair",
]
category: Literal[
    "model_output",
    "environment",
    "source",
    "linker",
    "timeout",
    "unknown",
    "authentication",
    "rate_limit",
    "service",
]
```

- [x] **Step 5: Run focused and full tests**

Expected: the Domain tests and all existing tests pass.

- [x] **Step 6: Save the Domain vocabulary checkpoint**

Commit only the Domain model and its tests as:

```text
feat: extend workflow capability errors
```

---

### Task 2: Add the Single Application Error Boundary

**Files:**

- Learner creates: `src/luxar/application/runner.py`
- Codex creates: `tests/application/test_runner.py`

**Interfaces:**

```python
def capability_error_to_workflow_error(
    error: CapabilityError,
    state: WorkflowState,
) -> WorkflowError: ...


def run_workflow(
    *,
    initial_state: WorkflowState,
    context: RuntimeContext,
) -> WorkflowState: ...
```

- [ ] **Step 1: Codex adds failing pure-mapping tests**

Cover this exact mapping:

```python
{
    "authentication": "authentication",
    "timeout": "timeout",
    "rate_limit": "rate_limit",
    "service": "service",
    "empty_response": "model_output",
    "invalid_json": "model_output",
    "invalid_schema": "model_output",
}
```

Test stage selection with three States:

```python
{"task_text": "..."}                                  # requirement_analysis
{"task_text": "...", "requirement": requirement}     # planning
{"task_text": "...", "requirement": requirement,
 "plan": plan, "build_evidence": evidence}             # repair
```

Each test injects a sensitive marker into `CapabilityError.message` and asserts that marker is absent from `WorkflowError.message` and `user_suggestion`.

- [ ] **Step 2: Codex adds failing Runner integration tests**

Use test-local Port implementations that raise configured `CapabilityError` values. Prove:

- requirement failure returns `status="failed"` and preserves `task_text`;
- planning failure preserves the parsed `FirmwareRequirement`;
- repair failure preserves the failed `BuildEvidence`, diagnostics, attempts, and plan;
- success still reaches `status="completed"`;
- the returned trace ends in `"failed"` for handled failures.

- [ ] **Step 3: Run the focused test and verify RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/application/test_runner.py
```

Expected: collection fails because `luxar.application.runner` does not exist.

- [ ] **Step 4: Teach the Runner call chain and syntax**

Explain:

```text
run_workflow
  -> build_graph
  -> graph.stream(..., stream_mode="values")
  -> latest_state is replaced after every successful Graph step
  -> one CapabilityError is caught outside all nodes
  -> pure mapping creates WorkflowError
  -> failed(latest_state) supplies status and trace update
  -> merged failed WorkflowState is returned
```

Also explain generator iteration, `try/except`, dictionary unpacking order, and why `latest_state` starts as a copy of `initial_state`.

- [ ] **Step 5: Learner implements fixed-message mapping**

Use application-owned dictionaries for category, message, and suggestion. Do not copy `error.message`. Preserve only `error.retryable`.

Stage selection is:

```python
if "requirement" not in state:
    stage = "requirement_analysis"
elif "plan" not in state:
    stage = "planning"
else:
    stage = "repair"
```

- [ ] **Step 6: Learner implements the one-catch Runner**

The implementation follows this exact control flow:

```python
latest_state = cast(WorkflowState, dict(initial_state))

try:
    for snapshot in build_graph().stream(
        initial_state,
        context=context,
        stream_mode="values",
    ):
        latest_state = cast(WorkflowState, snapshot)
except CapabilityError as error:
    workflow_error = capability_error_to_workflow_error(
        error,
        latest_state,
    )
    failure_update = failed(latest_state)
    return cast(
        WorkflowState,
        {
            **latest_state,
            "error": workflow_error,
            **failure_update,
        },
    )

return latest_state
```

- [ ] **Step 7: Run focused tests and inspect visible output**

Expected: all Runner cases pass without a network call. Explain each pytest case and the State it proves.

- [ ] **Step 8: Re-run the unchanged Graph topology test and full suite**

Expected: topology remains unchanged and the complete offline suite passes.

- [ ] **Step 9: Save the Runner checkpoint**

Commit the Runner and tests as:

```text
feat: handle capability failures at workflow boundary
```

---

### Task 3: Add the Opt-In Real DeepSeek Smoke Test

**Files:**

- Codex creates: `tests/smoke/test_deepseek_requirement_parser.py`

**Interfaces:**

- Default test execution skips without network access.
- Explicit execution uses `DeepSeekSettings`, `DeepSeekJsonClient`, and `DeepSeekRequirementParser` directly.

- [ ] **Step 1: Codex writes the smoke gate and one-call assertion**

The test uses both conditions:

```python
RUN_SMOKE = os.getenv("LUXAR_RUN_DEEPSEEK_SMOKE") == "1"
HAS_KEY = bool(os.getenv("DEEPSEEK_API_KEY"))

pytestmark = pytest.mark.skipif(
    not (RUN_SMOKE and HAS_KEY),
    reason=(
        "requires DEEPSEEK_API_KEY and "
        "LUXAR_RUN_DEEPSEEK_SMOKE=1"
    ),
)
```

The request is a minimal ESP32 GPIO-blink requirement. Assertions inspect only the validated `FirmwareRequirement`, never the raw response or secret.

- [ ] **Step 2: Run the smoke file without opt-in**

Expected: one skipped test and zero network calls.

- [ ] **Step 3: Run the complete offline suite**

Expected: all ordinary tests pass and the smoke remains skipped.

- [ ] **Step 4: Do not run the real request unless the learner explicitly enables it**

If both environment values are present but the learner has not explicitly requested quota use, leave the test skipped and report the exact opt-in command instead.

---

### Task 4: Final Security, Documentation, and Plan Synchronization

**Files:**

- Codex modifies: `README.md`
- Codex modifies: `docs/learning/PROGRESS.md`
- Codex modifies: `docs/superpowers/plans/2026-08-01-luxar-deepseek-adapters-plan.md`
- Codex modifies: this plan

- [ ] **Step 1: Document the production entry chain**

Record:

```text
build_deepseek_runtime_context(...) -> RuntimeContext
run_workflow(initial_state=..., context=...) -> WorkflowState
```

Explain the smoke opt-in without including an actual API key.

- [ ] **Step 2: Run dependency and secret-leak searches**

Verify only `src/luxar/adapters/deepseek/` imports `openai`. Search tracked files for common API-key prefixes and confirm no secret value, raw response body, or local absolute repair path was added.

- [ ] **Step 3: Run final verification**

Run the complete required pytest command and `git diff --check`. Read the exit code and exact pass/skip totals before claiming completion.

- [ ] **Step 4: Synchronize both plans and learning progress**

Mark completed checkboxes, record the verified counts, and identify the next enterprise slice without asking the learner to edit Markdown.

- [ ] **Step 5: Save the completed DeepSeek Adapter slice**

Commit documentation and final test adjustments as:

```text
feat: complete DeepSeek adapter slice
```

## Final Gate

1. Default tests pass with network access disabled.
2. The application contains exactly one `except CapabilityError` boundary.
3. Provider errors become sanitized failed State values and do not escape production workflow calls.
4. Planning and repair failures preserve the latest validated State and build evidence.
5. The existing seven-node Graph topology and business nodes remain unchanged.
6. Only DeepSeek Adapter modules import `openai`.
7. The real API smoke test requires two explicit environment opt-ins.
