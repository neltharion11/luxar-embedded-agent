# LUXAR v0.2.0 Migration Checklist

## Fully Deprecated Targets

- `src/luxar/prompts/*` as the primary control plane
- prompt-first orchestration in `src/luxar/core/*` generators and fixers
- hard gate driven execution as the default recovery model

## Compatibility Shells To Keep Temporarily

- legacy CLI commands in `src/luxar/cli.py`
- legacy API endpoints in `src/luxar/server/app.py`
- legacy build / flash / monitor workers, reused behind new workspace and harness adapters

## Test Migration Work

- Reclassify old prompt/gate tests into compatibility coverage or delete them once the runtime migration is complete
- Add dedicated tests for `agent`, `skills`, `harness`, `memory`, and `workspace` primitives
- Replace workflow-centric tests with runtime loop, artifact promotion, and harness-first integration tests

## Structural Guardrails

- New capabilities must land in one of: `agent`, `skills`, `harness`, `memory`, `tools`, `api/cli`
- Do not add new prompt-first modules
- Do not add new parallel workflow systems outside the vNext runtime
