# LUXAR Web UI and API Implementation Plan

Authority: `docs/superpowers/specs/2026-08-14-luxar-web-ui-api-design.md`

## Task 1: Web dependencies and contracts

- [ ] Add bounded FastAPI and Uvicorn dependencies.
- [ ] Add validated task request and safe response/event models.
- [ ] Reuse or extract the CLI allowlisted State serializer without widening
  its output.
- [ ] Test whitespace, attempt bounds, stream mode and explicit dependency
  authorization.

## Task 2: Safe project catalog

- [ ] Implement an explicit projects-root configuration.
- [ ] List sorted direct-child ESP-IDF projects without absolute paths.
- [ ] Resolve project names with lexical and resolved containment checks.
- [ ] Reject separators, traversal, links/Junctions, missing projects and
  invalid ESP-IDF roots.
- [ ] Add focused Windows-aware path tests.

## Task 3: FastAPI application and static UI

- [ ] Add an app factory with injected Runner/Bootstrap seams for offline tests.
- [ ] Serve the migrated UI and fixed health endpoint.
- [ ] Add safe project and compatibility conversation endpoints.
- [ ] Add `luxar-web` startup parsing with loopback defaults.
- [ ] Test static delivery and endpoint validation.

## Task 4: SSE workflow bridge

- [ ] Run the synchronous Runner outside the async event loop.
- [ ] Translate only `WorkflowProgress` to safe progress events.
- [ ] Emit one allowlisted final result followed by done.
- [ ] Sanitize startup errors.
- [ ] Bound queues and active workers.
- [ ] Reject concurrent workflows for the same project.
- [ ] Test event order, final states, failure behavior and data exclusion.

## Task 5: Original UI migration

- [ ] Copy the original UI asset into the clean repository.
- [ ] Replace legacy project and conversation calls with the new contracts.
- [ ] Render safe progress and final LangGraph results.
- [ ] Disable or label unsupported Driver, Skill, model-config, project mutation,
  attachment, serial and hardware controls.
- [ ] Ensure the Stop control does not claim backend cancellation.
- [ ] Add static checks for enabled endpoint references.

## Task 6: Verification and learning synchronization

- [ ] Run the required complete pytest command.
- [ ] Audit the API/UI for secrets, absolute paths and legacy backend imports.
- [ ] Confirm the seven-node topology is unchanged.
- [ ] Verify installed `luxar-web --help` and local app startup.
- [ ] Update README, progress record and one consolidated Chinese learning
  chapter without staging unrelated learner documentation changes.
- [ ] Commit only intentional files; leave `.vscode/` and learner-owned document
  reorganization untouched.

## Final gate

- [ ] Existing contained project can run through UI → API/SSE → Runner → Graph.
- [ ] Safe progress and final result are visible.
- [ ] Unsupported legacy features cannot call missing legacy endpoints.
- [ ] Complete offline suite and security audits pass.

