# LUXAR DeepSeek Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not delegate the learner's core coding exercises to subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the unchanged LUXAR LangGraph workflow to the real DeepSeek API through tested, structured, provider-isolated Adapters.

**Architecture:** An OpenAI-compatible SDK wrapper talks only to `api.deepseek.com` and returns JSON dictionaries through an internal Protocol. Three business Adapters convert those dictionaries into existing Pydantic Domain objects. Runtime Context receives the production Adapters at invocation time, leaving the Graph topology unchanged.

**Tech Stack:** Python 3.12, LangGraph `>=1.2,<1.3`, Pydantic `>=2,<3`, pydantic-settings `>=2,<3`, openai `>=2,<3`, pytest `>=8,<9`.

## Global Constraints

- Repository: `C:\tmp\luxar-langgraph`.
- Tests: `C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider`.
- Default tests perform no network calls and consume no API quota.
- Real smoke requires both `DEEPSEEK_API_KEY` and `LUXAR_RUN_DEEPSEEK_SMOKE=1`.
- The API key comes only from the environment and never enters Git, State, logs, prompts, or test output.
- Only `src/luxar/adapters/deepseek/` may import `openai`.
- Current models are `deepseek-v4-flash` and `deepseek-v4-pro`; do not use retired aliases.
- The learner writes settings/error boundaries, client behavior, and the three business Adapters. Codex writes tests, fixtures, README, architecture notes, and progress records.
- Teach each call chain and exact syntax before learner implementation; do not use prediction quizzes or deliberately broken teaching code.
- Preserve all existing Domain, Port, node, routing, and Graph signatures.

---

### Task 1: Add Dependencies, Settings, and Stable Errors

**Files:**

- Codex modifies: `pyproject.toml`
- Learner creates: `src/luxar/adapters/deepseek/settings.py`
- Learner creates: `src/luxar/ports/errors.py`
- Codex creates: `tests/adapters/deepseek/test_settings.py`
- Codex creates: `tests/ports/test_capability_errors.py`

**Interfaces:**

- `DeepSeekSettings(BaseSettings)` exposes secret-safe API key, base URL, fast model, repair model, and positive timeout.
- `CapabilityError(RuntimeError)` exposes stable `category`, `message`, and `retryable` fields.

- [x] Codex adds `openai>=2,<3` and `pydantic-settings>=2,<3` dependencies and installs the editable project.
- [x] Codex teaches `BaseSettings`, environment prefixes, `SecretStr`, and why no `.env` is generated.
- [x] Learner implements `DeepSeekSettings` with `SettingsConfigDict(env_prefix="DEEPSEEK_", extra="ignore")`.
- [x] Codex teaches stable capability exceptions versus SDK exception leakage.
- [x] Learner implements `CapabilityErrorCategory` and `CapabilityError`.
- [x] Codex adds tests for defaults, environment overrides, missing keys, secret-safe repr, categories, and retryability.
- [x] Run focused tests and save `feat: configure DeepSeek capability boundary`.

---

### Task 2: Implement the Shared JSON Client Boundary

**Files:**

- Learner creates: `src/luxar/adapters/deepseek/client.py`
- Learner creates: `src/luxar/adapters/deepseek/fake_client.py`
- Codex creates: `tests/adapters/deepseek/test_client.py`
- Codex creates: `tests/adapters/deepseek/test_fake_client.py`

**Interfaces:**

```python
class JsonCompletionClient(Protocol):
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> dict[str, object]: ...
```

- [x] Teach SDK wrapper versus business Adapter and constructor injection of a mocked SDK object.
- [x] Learner defines `JsonCompletionClient` and `DeepSeekJsonClient`.
- [x] Client sends Chat Completions messages and `response_format={"type": "json_object"}`.
- [x] Client rejects missing choices, empty content, invalid JSON, and non-object JSON through `CapabilityError`.
- [x] Client maps SDK authentication, timeout, rate-limit, connection, and status errors without raw sensitive bodies.
- [x] Learner implements a sequenced `FakeJsonCompletionClient` with call recording.
- [x] Codex adds all success/error mapping tests and verifies no network call occurs.
- [x] Run focused tests and save `feat: wrap DeepSeek JSON completions`.

---

### Task 3: Implement `DeepSeekRequirementParser`

**Files:**

- Learner creates: `src/luxar/adapters/deepseek/requirement_parser.py`
- Codex creates: `tests/adapters/deepseek/test_requirement_parser.py`
- Codex creates: `docs/learning/06-deepseek-structured-output.md`

**Interfaces:**

- Implements existing `RequirementParser.parse(task_text) -> FirmwareRequirement`.
- Constructor receives `JsonCompletionClient` and model name.

- [x] Teach schema-guided JSON output and why JSON validity is weaker than Domain validity.
- [x] Learner builds a narrow system prompt containing `FirmwareRequirement.model_json_schema()`.
- [x] Learner serializes the user task through `json.dumps` and validates with `FirmwareRequirement.model_validate()`.
- [x] Invalid schema becomes `CapabilityError(category="invalid_schema", retryable=False)`.
- [x] Codex tests complete and incomplete requirements, prompt/model recording, unsupported platform, missing required fields, and extra prose avoidance.
- [x] Run focused and full tests; save `feat: parse firmware requirements with DeepSeek`.

---

### Task 4: Implement `DeepSeekPlanner`

**Files:**

- Learner creates: `src/luxar/adapters/deepseek/planner.py`
- Codex creates: `tests/adapters/deepseek/test_planner.py`

**Interfaces:**

- Implements existing `Planner.create_plan(requirement) -> ExecutionPlan`.
- Constructor receives `JsonCompletionClient` and model name.

- [x] Teach validated input serialization and action-vocabulary enforcement.
- [x] Learner sends `requirement.model_dump(mode="json")` and `ExecutionPlan.model_json_schema()`.
- [x] Learner validates with `ExecutionPlan.model_validate()` and normalizes schema errors.
- [x] Codex tests valid ordered steps, empty plans, unsupported actions, and exact client calls.
- [x] Run focused and full tests; save `feat: create execution plans with DeepSeek`.

---

### Task 5: Implement `DeepSeekRepairPlanner`

**Files:**

- Learner creates: `src/luxar/adapters/deepseek/repair_planner.py`
- Codex creates: `tests/adapters/deepseek/test_repair_planner.py`

**Interfaces:**

- Implements existing four-argument `RepairPlanner.create_repair(...) -> RepairPlan`.
- Constructor receives `JsonCompletionClient` and repair model name.

- [x] Trace requirement, plan, evidence/diagnostics, and project files into one JSON user payload.
- [x] Learner includes `RepairPlan.model_json_schema()` and requires complete-file replacements.
- [x] Learner validates with `RepairPlan.model_validate()`; unsafe or duplicate paths become non-retryable `invalid_schema` errors.
- [x] Codex tests diagnostic/file propagation, complete replacement content, path rejection, and exact repair-model selection.
- [x] Run focused and full tests; save `feat: plan evidence-driven repairs with DeepSeek`.

---

### Task 6: Bootstrap DeepSeek into the Unchanged Graph

**Files:**

- Learner creates: `src/luxar/bootstrap.py`
- Codex creates: `tests/test_bootstrap.py`
- Codex modifies: `tests/integration/test_fake_vertical_slice.py` only if shared fixtures are extracted without changing behavior.

**Interfaces:**

- Produces a composition function that receives `EspIdfPort`, `WorkspacePort`, and `project_path`, loads settings, builds one shared client, constructs three DeepSeek Adapters, and returns `RuntimeContext`.

- [x] Teach composition root and prove why Graph code must not import DeepSeek.
- [x] Learner implements production Context construction with fast model for requirement/planning and repair model for repair.
- [x] Codex tests exact Adapter/client identity and models without network access.
- [x] Re-run Graph topology test unchanged.
- [x] Save `feat: compose DeepSeek runtime adapters`.

---

### Task 7: Add Optional Real API Smoke and Error Integration

**Files:**

- Codex creates: `tests/smoke/test_deepseek_requirement_parser.py`
- Learner modifies the centralized application error boundary selected during teaching.
- Codex adds matching application and integration tests.
- Codex updates: `README.md`, `docs/learning/PROGRESS.md`, and architecture notes.

**Interfaces:**

- Smoke test skips unless both explicit environment conditions are true.
- Stable `CapabilityError` becomes a sanitized `WorkflowError` and explicit failed State without provider leakage.

- [ ] Codex writes the opt-in smoke test; it sends one minimal requirement request and never prints the key or raw response.
- [ ] Teach centralized capability-error handling and select the smallest LangGraph-compatible boundary.
- [ ] Learner implements the approved error-to-State path.
- [ ] Codex tests authentication/timeout/service/schema failure termination and evidence preservation.
- [ ] Run the full offline suite and dependency-leak searches.
- [ ] If explicitly enabled by the learner, run the one-call real smoke test.
- [ ] Record final checkpoint and save `feat: complete DeepSeek adapter slice`.

## Final Gate

1. Default tests pass with the network unavailable.
2. Only DeepSeek Adapter modules import `openai`.
3. API secrets do not appear in State, prompts, logs, snapshots, or Git diff.
4. All three business Adapters return existing Domain models through existing Ports.
5. The existing compiled Graph and topology test remain unchanged.
6. SDK and output failures become stable capability errors.
7. The learner can trace one natural-language task from Runtime Context through DeepSeek JSON into validated State.
