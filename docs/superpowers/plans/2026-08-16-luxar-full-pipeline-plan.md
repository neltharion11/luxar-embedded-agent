# LUXAR Full-Pipeline Agent Plan

Status: proposed on 2026-08-16

Companion to
`docs/superpowers/specs/2026-08-16-luxar-full-pipeline-design.md`. Slices land
sequentially; each keeps the complete offline suite green before the next
starts. Real-hardware smokes stay opt-in and are attempted in S6 only after
the ESP-IDF toolchain is installed on this machine.

## S1 — Plan execution engine

Domain and application only; no new external capability.

- Extend `PlanStep.kind` to the four kinds and add the `ExecutionPlan`
  ordering validator in `src/luxar/domain/plans.py`.
- Add cursor state (`plan_index`, `repair_origin`) and
  `execute_next_step` node plus `route_after_dispatch` in
  `src/luxar/application/`.
- Rebuild the graph edges: `create_plan → execute_next_step`; dispatch
  routes to `create_project`/`build_project`/`flash_project`/
  `monitor_project` placeholder behavior is NOT added yet — S1 wires
  `build_project` and keeps the other kinds routing to a temporarily
  terminal `failed` with a fixed "step not yet supported" workflow error,
  replaced slice by slice.
- Update `route_after_build`: success routes to the dispatcher (monitor
  origin handling lands in S4).

Tests: plan vocabulary/ordering validators; dispatcher order, plan
completion, single-step plans; build-only plans behave identically to the
previous topology; unsupported-step terminal behavior.

Verification: `pytest -v -p no:cacheprovider` stays fully green; topology
test updated to the new edge set.

## S2 — Project creation

- `src/luxar/domain/projects.py`: `ProjectEvidence` + consistency
  validator.
- `src/luxar/ports/espidf_project.py`: `EspIdfProjectPort`.
- Extract shared preflight/sanitization internals from `espidf_cli.py` into
  `src/luxar/adapters/espidf_common.py` (pure refactor, no behavior change;
  existing adapter tests prove it).
- `src/luxar/adapters/espidf_project.py`: `EspIdfProjectAdapter`
  (`idf.py create-project`, containment, `sdkconfig.defaults` target write).
- `src/luxar/adapters/fake_project_creator.py`.
- `create_project` node in `src/luxar/application/nodes.py`; dispatch
  wiring replaces the S1 placeholder; `route_after_dispatch` returns to
  the dispatcher after success.
- Context/bootstrap gains `project_creator`; CLI `--project` may point at a
  not-yet-existing directory (parent must exist); `--target` validation
  for creation tasks.

Tests: evidence consistency; port contract; adapter containment
(link/parent/duplicate rejection), fake subprocess success/failure/timeout,
`sdkconfig.defaults` content, unsupported `create-project` sanitized error;
node call-through; Fake vertical slice create→build→completed; CLI target
guards.

## S3 — Flash with human approval

- `src/luxar/domain/devices.py`: `SerialPortInfo`, `FlashEvidence`,
  `ApprovalRequest`.
- `src/luxar/ports/espidf_device.py`: `EspIdfFlashPort`
  (`discover_serial_ports`, `flash`).
- `src/luxar/adapters/espidf_device.py`: `EspIdfDeviceAdapter` flash
  portion (preflight reuse, `idf.py -p <port> flash`, port-name pattern
  validation, evidence categories). Add bounded `pyserial` dependency.
- `request_flash_approval` + `flash_project` nodes; `route_after_approval`
  and `route_after_flash`; `flash_attempts` retry budget (2).
- Runner: `BaseCheckpointSaver` through `RuntimeContext` (default
  `InMemorySaver`), `GraphInterrupt` handling, `approval_handler` seam,
  `resume_workflow(thread_id, approved)`, `WorkflowRunResult`. Verify the
  exact `interrupt()` semantics of the installed `langgraph>=1.2,<1.3` and
  lock them with tests.
- `WorkflowError.category` gains `approval_rejected`; runner maps fixed
  message/suggestion text. Result envelope gains `flash_evidence` and
  `approval_status`.

Tests: evidence/port model validation; port-name pattern matrix; flash
adapter success/timeout/serial/environment with injected subprocess;
approval pause produces the sanitized request and persists state;
resume-approved executes exactly one flash; resume-rejected terminates
with `approval_rejected`; approval is not re-requested on the second flash
of one run; retry budget exhaustion; runner resume on unknown thread id is
sanitized; envelope contains no absolute paths or raw logs.

## S4 — Serial monitor and AI log-analysis loop

- `src/luxar/domain/devices.py`: `MonitorEvidence`,
  `DeviceLogDiagnostic`, `DeviceDiagnosis`.
- `src/luxar/ports/espidf_device.py`: `EspIdfMonitorPort`; new
  `src/luxar/ports/log_analyst.py`: `LogAnalystPort`.
- `EspIdfDeviceAdapter.monitor`: process-group start, capture-window
  termination, sanitization, ESP32 pattern parser.
- `src/luxar/adapters/deepseek/log_analyst.py`: `DeepSeekLogAnalyst`
  (repair model, untrusted-log prompt rules, schema validation).
- Fakes: `FakeMonitor`, `FakeLogAnalyst`.
- Nodes `monitor_project` and `analyze_device_logs`;
  `route_after_diagnosis`; `device_cycles` budget (default 3).
- `RepairPlanner.create_repair` gains an optional
  `device_diagnosis: DeviceDiagnosis | None = None` parameter; the
  DeepSeek repair adapter includes it in the repair context when present;
  Fakes updated. `repair_project` sets `repair_origin="monitor"` when
  diagnosis-driven, and `route_after_build` success routes to
  `flash_project` for monitor-origin repairs (approval already granted).
- Bootstrap wires monitor, log analyst, and the shared device adapter.

Tests: monitor evidence bounds and diagnostics parser pattern matrix
(panic/abort/assert/watchdog/boot loop); adapter timeout capture and
sanitization; analyst prompt and schema behavior with fake JSON client;
diagnosis routing (healthy completes, repair_needed repairs, budget
exhaustion fails); full Fake vertical slice of the device loop;
repair-planner diagnosis propagation; unchanged build-only regression.

## S5 — CLI and Web integration

- CLI: `luxar ports`; `--port`/`--target` plumbing; interactive approval
  prompt; JSON mode `--approve-flash` guard; new exit/help text.
- Web: `approval` SSE event (allowlisted request + thread id); stream
  stays open; `POST /api/conversations/{project}/approval` resumes
  in-process; result envelope and UI wiring for new evidence fields;
  hardware controls remain server-side configuration.
- `results.py` allowlist extension; web contract tests.

Tests: CLI port listing, approval accept/reject paths, JSON guard,
invalid-port rejection; Web approval request/response contract, exact SSE
event order through the paused run, resume decision propagation, same
Runner/Bootstrap sharing; no secrets or absolute paths in new payloads.

## S6 — Docs, audits, and real-hardware smoke

- Learning notes (`docs/learning/11-full-pipeline.md` and updates to the
  review overview `00`), `PROGRESS.md` checkpoint entry.
- Security searches: subprocess confinement, shell-string absence, unsafe
  YAML absence, single error boundary, approval/envelope sanitization.
- Install the ESP-IDF toolchain on this machine (user-authorized,
  multi-GB) once slices S1–S5 are green.
- Opt-in smokes behind `LUXAR_RUN_ESPIDF_SMOKE=1` and `LUXAR_ESP32_PORT`:
  real flash and real monitor capture on the connected CH340 board;
  document exact commands and expected evidence.
- README milestone section update; full regression verification
  (expected: 290+ collected, all offline tests passing, smokes skipped by
  default).

## Ordering rationale

S1 makes the plan the execution contract with zero new hardware risk.
S2 and S3 grow tool capabilities one Port at a time; S3 introduces the
only new orchestration primitive (interrupt/resume) in isolation. S4
closes the device loop on top of the verified approval flow. S5 adapts
presentation last so the application contract is stable. S6 verifies
against real hardware only after the toolchain exists.

## Verification commands

```bat
D:\anaconda\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]" pyserial
.venv\Scripts\python.exe -m pytest -v -p no:cacheprovider
```

(Development environment: this machine has no `luxar-learning` conda env;
a project-local venv at the repository root is used instead. The venv is
gitignored.)
