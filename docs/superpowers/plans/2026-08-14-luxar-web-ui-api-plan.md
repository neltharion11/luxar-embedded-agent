# LUXAR Web UI and API Implementation Plan

Authority: `docs/superpowers/specs/2026-08-14-luxar-web-ui-api-design.md`

## Task 1: Web dependencies and contracts

- [x] Add bounded FastAPI and Uvicorn dependencies.
- [x] Add validated task request and safe response/event models.
- [x] Reuse or extract the CLI allowlisted State serializer without widening
  its output.
- [x] Test whitespace, attempt bounds, stream mode and explicit dependency
  authorization.

## Task 2: Safe project catalog

- [x] Implement an explicit projects-root configuration.
- [x] List sorted direct-child ESP-IDF projects without absolute paths.
- [x] Resolve project names with lexical and resolved containment checks.
- [x] Reject separators, traversal, links/Junctions, missing projects and
  invalid ESP-IDF roots.
- [x] Add focused Windows-aware path tests through the shared Windows-capable
  path checks and Web path-syntax/catalog suite.

## Task 3: FastAPI application and static UI

- [x] Add an app factory with injected Runner/Bootstrap seams for offline tests.
- [x] Serve the migrated UI and fixed health endpoint.
- [x] Add safe project and compatibility conversation endpoints.
- [x] Add `luxar-web` startup parsing with loopback defaults.
- [x] Test static delivery and endpoint validation.

## Task 4: SSE workflow bridge

- [x] Run the synchronous Runner outside the async event loop.
- [x] Translate only `WorkflowProgress` to safe progress events.
- [x] Emit one allowlisted final result followed by done.
- [x] Sanitize startup errors.
- [x] Bound queues and active workers.
- [x] Reject concurrent workflows for the same project.
- [x] Test event order, final states, failure behavior and data exclusion.

## Task 5: Original UI migration

- [x] Copy the original UI asset into the clean repository.
- [x] Replace legacy project and conversation calls with the new contracts.
- [x] Render safe progress and final LangGraph results.
- [x] Disable or label unsupported Driver, Skill, model-config, project mutation,
  attachment, serial and hardware controls.
- [x] Ensure the Stop control does not claim backend cancellation.
- [x] Add static checks for enabled endpoint references.

## Task 6: Verification and learning synchronization

- [x] Run the required complete pytest command.
- [x] Audit the API/UI for secrets, absolute paths and legacy backend imports.
- [x] Confirm the seven-node topology is unchanged.
- [x] Verify installed `luxar-web --help` and local app startup.
- [x] Add one consolidated Chinese learning chapter. README and progress updates
  remain intentionally deferred because learner-owned reorganizations overlap
  those files.
- [x] Commit only intentional files; leave `.vscode/` and learner-owned document
  reorganization untouched.

## Final gate

- [x] Existing contained project can run through UI → API/SSE → Runner → Graph.
- [x] Safe progress and final result are visible.
- [x] Unsupported legacy features cannot call missing legacy endpoints.
- [x] Complete offline suite and security audits pass.
