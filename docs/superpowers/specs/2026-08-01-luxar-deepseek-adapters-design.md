# LUXAR DeepSeek Adapters Design

**Date:** 2026-08-01

**Status:** Approved by learner

**Repository:** `C:\tmp\luxar-langgraph`

## 1. Goal

Replace the three model-shaped Fakes with production DeepSeek API Adapters while preserving the existing Domain, Ports, Runtime Context, nodes, routing, and seven-node Graph topology.

The public architecture remains:

```text
LangGraph + DeepSeek API + custom Ports
```

The `openai` Python package is used only as an OpenAI-compatible transport client pointed at `https://api.deepseek.com`. No request is sent to OpenAI services.

## 2. Scope

This slice adds:

- Environment-backed DeepSeek settings with secret-safe representation.
- Stable capability errors independent of the transport SDK.
- A shared JSON completion client and deterministic Fake client.
- `DeepSeekRequirementParser`.
- `DeepSeekPlanner`.
- `DeepSeekRepairPlanner`.
- Mocked response tests for every success and failure class.
- An explicitly enabled, optional real API smoke test.
- A bootstrap function that injects DeepSeek Adapters into the unchanged Graph.

This slice does not add a real filesystem Adapter, real `idf.py`, persistence, human approval, Web UI, or automatic production retries.

## 3. Current Official API Assumptions

As of 2026-08-01, the official OpenAI-format base URL is `https://api.deepseek.com`. Supported current model identifiers are `deepseek-v4-flash` and `deepseek-v4-pro`; the legacy aliases `deepseek-chat` and `deepseek-reasoner` were retired on 2026-07-24.

JSON requests use Chat Completions with:

```python
response_format={"type": "json_object"}
```

The prompt must explicitly request JSON. JSON mode can still return empty content, so empty responses are a first-class tested error.

## 4. Dependency Boundary

```text
Domain / Ports / Application / Graph
              ↑
      business Port interfaces
              ↑
DeepSeekRequirementParser  DeepSeekPlanner  DeepSeekRepairPlanner
              ↓
       JsonCompletionClient
              ↓
       DeepSeekJsonClient
              ↓
 OpenAI-compatible SDK → api.deepseek.com
```

Only `src/luxar/adapters/deepseek/` imports `openai`. Domain, Ports, Application, and Graph remain provider-independent.

## 5. Configuration

`DeepSeekSettings(BaseSettings)` reads only environment variables:

| Environment variable | Field | Rule |
|---|---|---|
| `DEEPSEEK_API_KEY` | `api_key: SecretStr` | Required |
| `DEEPSEEK_BASE_URL` | `base_url: str` | Default `https://api.deepseek.com` |
| `DEEPSEEK_FAST_MODEL` | `fast_model: str` | Default `deepseek-v4-flash` |
| `DEEPSEEK_REPAIR_MODEL` | `repair_model: str` | Default `deepseek-v4-pro` |
| `DEEPSEEK_TIMEOUT_SECONDS` | `timeout_seconds: float` | Default 60, greater than zero |

No `.env` file is generated. API keys never enter State, checkpoints, logs, test snapshots, prompts, Markdown, or Git.

## 6. Stable Capability Errors

`src/luxar/ports/errors.py` defines `CapabilityError`, independent of DeepSeek and OpenAI SDK types. It carries:

- `category`: `authentication`, `timeout`, `rate_limit`, `service`, `empty_response`, `invalid_json`, or `invalid_schema`.
- `message`: sanitized operational description.
- `retryable`: stable recovery fact.

The DeepSeek client translates SDK authentication, timeout, rate-limit, connection, and status failures. Business Adapters translate malformed JSON shape and Pydantic validation failures. Raw response bodies and secrets are not included in exception messages.

## 7. Shared JSON Client

The internal `JsonCompletionClient` Protocol exposes:

```python
def complete_json(
    self,
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
) -> dict[str, object]: ...
```

`DeepSeekJsonClient` owns SDK construction, Chat Completions request parameters, content extraction, `json.loads`, top-level object validation, and SDK error normalization. It has no knowledge of LUXAR Domain models.

`FakeJsonCompletionClient` returns configured dictionaries in sequence and records prompts/model names. It makes Adapter tests deterministic and consumes no API quota.

## 8. Business Adapters

Each Adapter receives `JsonCompletionClient` and an explicit model name through its constructor.

### Requirement Parser

The prompt includes `FirmwareRequirement.model_json_schema()` and the user task. It requires explicit `missing_fields` when target, feature, or necessary GPIO information cannot be determined. The result is validated with `FirmwareRequirement.model_validate()`.

### Planner

The prompt includes the validated requirement and `ExecutionPlan.model_json_schema()`. The result is validated with `ExecutionPlan.model_validate()`. Unsupported steps remain impossible because the Domain model owns the action vocabulary.

### Repair Planner

The prompt includes the validated requirement, execution plan, last build evidence with diagnostics, validated project files, and `RepairPlan.model_json_schema()`. It instructs the model to return complete file contents and project-relative paths only. `RepairPlan.model_validate()` rechecks all path invariants.

The model cannot access the filesystem, apply changes, run commands, or produce successful `BuildEvidence`.

## 9. Prompt and Data Rules

- System prompts define one narrow role and explicitly require one JSON object with no prose wrapper.
- User payloads are produced through `model_dump(mode="json")` and `json.dumps`, not hand-built interpolation of Python representations.
- Pydantic JSON schemas are included so expected fields and constraints are explicit.
- The Workspace Adapter remains responsible for file allowlists and context size caps before repair files reach the model.
- API keys and transport exception bodies never appear in prompts.

## 10. Testing and Smoke Boundary

Default pytest runs never call the network. Tests inject Fake clients and cover:

- Correct model and prompt payload selection.
- Valid requirement, plan, and repair conversion.
- Empty content.
- Invalid JSON.
- Non-object JSON.
- Pydantic schema rejection.
- Authentication, timeout, rate-limit, connection, and service error normalization.
- Secret-safe settings representation.

The real smoke test is skipped unless both conditions hold:

```text
DEEPSEEK_API_KEY is set
LUXAR_RUN_DEEPSEEK_SMOKE=1
```

It sends one minimal requirement-parsing request with `deepseek-v4-flash`. It does not send source files and does not run automatically in the normal suite.

## 11. Bootstrap and Graph Stability

`bootstrap.py` constructs settings, the shared client, and the three DeepSeek business Adapters, then places them in `RuntimeContext` alongside the separately supplied `EspIdfPort`, `WorkspacePort`, and project path.

`build_graph()` remains unchanged. The same Graph that passed with Fakes runs with DeepSeek Adapters because both satisfy the existing Ports.

Graph-level handling of `CapabilityError` into `WorkflowError` is the final task in this slice. It must not duplicate provider-specific `try/except` blocks in every node.

## 12. Success Criteria

- The full default suite passes without network access or API quota.
- Only `adapters/deepseek/` imports the `openai` package.
- All three DeepSeek Adapters validate JSON into existing Domain objects.
- Every documented client/output error becomes a stable `CapabilityError`.
- The optional smoke test proves a real DeepSeek response can become `FirmwareRequirement`.
- The compiled Graph source and topology tests remain unchanged.
- No secret or raw sensitive response is committed or logged.

