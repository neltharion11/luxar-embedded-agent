# LUXAR EspIdfCliAdapter Design

**Date:** 2026-08-04

**Status:** Approved in teaching; pending user document review

**Repository:** `C:\tmp\luxar-langgraph`

## 1. Goal

Implement a production-shaped `EspIdfCliAdapter` for the existing `EspIdfPort`. It validates an ESP-IDF project and its dependency authorization, runs real `idf.py reconfigure` and `idf.py build` commands without a shell, and converts bounded, sanitized tool output into deterministic `BuildEvidence` for LangGraph routing and repair.

## 2. Scope

This slice adds:

- a real CLI Adapter implementing `EspIdfPort`;
- an explicit `allow_dependency_downloads=False` default;
- static project and manifest preflight before any possibly networked command;
- separate `reconfigure` and `build` phases;
- subprocess timeouts and bounded output capture;
- deterministic failure classification and compiler diagnostic parsing;
- project-relative diagnostic paths and sanitized summaries;
- a stable `EspIdfError` contract for failures before a command runs;
- `"dependency"` in build and workflow error categories;
- mocked-subprocess tests and an opt-in real ESP-IDF smoke test;
- teaching notes and synchronized progress records.

This slice does not install ESP-IDF, activate an ESP-IDF shell, create a firmware project, flash hardware, monitor a serial port, emulate the complete IDF Component Manager dependency solver, persist full build logs, or change the existing seven-node LangGraph topology.

## 3. Architecture Boundary

```text
bootstrap
  → creates EspIdfCliAdapter
  → stores it as RuntimeContext.espidf

LangGraph build_project node
  → EspIdfPort.build(project_path)
  → runtime object dispatches to EspIdfCliAdapter.build
  → deterministic preflight
  → idf.py reconfigure
  → idf.py build
  → BuildEvidence
  → WorkflowState.build_evidence
  → route_after_build
```

`EspIdfCliAdapter` is an Adapter class, not a LangGraph node. `build_project` remains the node. `EspIdfPort` remains the application-owned interface. Domain and Application modules do not import `subprocess`.

The Port signature remains:

```python
def build(self, project_path: Path) -> BuildEvidence:
    ...
```

Download authorization is fixed when the Adapter is constructed. It is not stored in `WorkflowState`, accepted from an LLM response, or modifiable by a graph node.

## 4. Constructor and Command Configuration

The production Adapter is configured conceptually as:

```python
EspIdfCliAdapter(
    idf_command=("idf.py",),
    allow_dependency_downloads=False,
    reconfigure_timeout_seconds=120,
    build_timeout_seconds=600,
    max_summary_chars=16_000,
)
```

All timeouts and output limits must be positive. `idf_command` must contain only non-empty strings and is copied to an immutable tuple.

The command prefix supports enterprise and Windows deployments where the ESP-IDF Python environment must be explicit, for example:

```python
(
    r"C:\Espressif\python_env\idf_env\Scripts\python.exe",
    r"C:\Espressif\frameworks\esp-idf\tools\idf.py",
)
```

The configured prefix is trusted application configuration. It never comes from task text, a prompt, a model response, or a project file. `BuildEvidence.command` records only the logical command such as `['idf.py', 'build']`, never absolute launcher paths.

The default test suite does not assume `idf.py` is installed. The current ordinary PowerShell environment has neither a discoverable `idf.py` nor `IDF_PATH`, so real smoke verification is opt-in.

## 5. Strict Dependency Authorization

ESP-IDF Component Manager may resolve and download registry or Git dependencies during CMake configuration. The official `IDF_COMPONENT_MANAGER=0` setting disables the manager as a whole; it is not a general-purpose "resolve normally but never access the network" mode.

Therefore the Adapter implements a conservative, provable policy.

### Default mode

With `allow_dependency_downloads=False`:

1. discover project-owned `idf_component.yml` files without following symbolic links or Windows junctions and without traversing generated, tool-owned, hidden, or managed-component directories;
2. parse each bounded UTF-8 manifest with `yaml.safe_load`;
3. treat a non-empty `dependencies` mapping as requiring Component Manager authorization;
4. if such a declaration exists, raise a stable non-retryable dependency error before invoking `idf.py`;
5. otherwise invoke commands with a copied environment containing `IDF_COMPONENT_MANAGER=0`.

This deliberately rejects projects with registry, Git, or manifest-declared local dependencies even when a local cache might already contain them. Running Component Manager could still perform network work, and disabling it would not faithfully interpret those manifests. The strict default favors a demonstrable permission boundary over cache-dependent convenience.

An absent manifest, an empty manifest, or a manifest without a non-empty `dependencies` mapping does not require download authorization. Ordinary project components declared through the ESP-IDF CMake build system can still build with Component Manager disabled.

Manifest discovery excludes at least:

```text
.git
.vscode
.idea
build
build_*
managed_components
__pycache__
hidden directories
```

Manifest size and aggregate scan limits prevent unbounded preflight reads. Invalid YAML, invalid UTF-8, an invalid top-level type, or a non-mapping `dependencies` value is an invalid-project preflight failure. Static inspection is an authorization gate, not a replacement dependency solver.

### Explicitly authorized mode

With `allow_dependency_downloads=True`, the Adapter leaves Component Manager enabled and allows `reconfigure` to resolve manifests, update tool-owned `dependencies.lock`, and populate tool-owned `managed_components` as ESP-IDF requires.

The Workspace Adapter and RepairPlanner continue to exclude `managed_components` and `dependencies.lock`; only ESP-IDF tooling may manage them.

### Future offline mirror mode

A separately configured local mirror may be added later using supported Component Manager profile settings. It is outside this slice and must not be inferred merely from the presence of a cache directory.

## 6. Pre-command Validation and EspIdfError

Before invoking a subprocess, the Adapter verifies:

- `project_path` exists, is a directory, resolves successfully, and is not a symbolic link or Windows junction;
- the project root contains a regular `CMakeLists.txt`;
- the first configured launcher token is an existing absolute executable or is discoverable through the trusted process environment;
- any configured absolute script path, such as an explicit `idf.py`, exists as a regular file;
- manifest preflight succeeds;
- dependency authorization permits the intended command.

Add a provider-independent `EspIdfError(RuntimeError)` contract with stable categories:

```text
invalid_project
environment
dependency
process
```

The error contains a fixed sanitized message and `retryable` flag. It does not expose absolute paths, raw operating-system errors, environment variables, command prefixes, or file contents.

These failures do not produce `BuildEvidence`, because no ESP-IDF command completed and inventing a command return code would create false evidence. The centralized Runner catches `EspIdfError` at its existing single workflow boundary and translates it to `WorkflowError(stage="build", ...)` while preserving the latest State.

Mapping rules:

```text
invalid_project → WorkflowError.category="environment", retryable=False
environment     → WorkflowError.category="environment", stable retryability
dependency      → WorkflowError.category="dependency", retryable=False
process         → WorkflowError.category="environment", stable retryability
```

Add `"dependency"` to `WorkflowError.category`. Application-owned messages tell the user whether to fix the project/environment or explicitly authorize dependency resolution.

## 7. Command Execution

After preflight, `build` runs two logical phases:

```text
idf.py reconfigure
idf.py build
```

Although `idf.py build` can configure automatically, the explicit first phase separates project/environment/dependency-resolution failures from compiler and linker failures.

Each phase uses `subprocess.run` with:

- an argument list assembled only from the trusted command prefix and fixed action;
- `cwd` set to the validated project root;
- `shell=False`;
- captured standard output and standard error;
- text decoding with replacement for undecodable tool bytes;
- a phase-specific timeout;
- a copied environment rather than global environment mutation;
- color and nonessential Component Manager hints disabled where supported.

The Adapter never executes project-provided shell text. A nonzero `reconfigure` result returns failure evidence immediately and prevents `build` from running. A successful `reconfigure` result is not separately stored in State; only the terminal phase evidence is returned. A zero-return-code `build` produces successful evidence.

`subprocess.TimeoutExpired` means the command started but did not finish, so it produces failed `BuildEvidence` with `return_code=-1` and `error_category="timeout"`. A failure to start the process produces `EspIdfError(category="process")` instead.

## 8. Evidence Classification

Add `"dependency"` to `BuildEvidence.error_category`.

Successful evidence has no error category. Failed command output is classified deterministically in this precedence order:

```text
timeout
dependency
environment
linker
source
unknown
```

The command phase provides additional context. Dependency resolution patterns are meaningful in `reconfigure`; compiler and linker patterns are meaningful in `build`. Classification uses bounded sanitized stdout and stderr together, not an LLM.

Representative signals include:

- dependency: registry/component resolution, lock solving, managed-component download, unavailable declared component;
- environment: missing CMake, Ninja, compiler, Python module, invalid IDF installation, unsupported tool setup;
- linker: undefined reference, multiple definition, linker collection failure;
- source: GCC/Clang error diagnostics and repairable project `CMakeLists.txt` diagnostics;
- timeout: Python timeout exception;
- unknown: nonzero exit without a reliable supported pattern.

Classification is intentionally conservative. Unknown output stays `unknown`; it is not guessed into a repairable category.

`route_after_build` changes only by treating `dependency` as terminal. The seven-node topology remains unchanged:

```text
success       → completed
source/linker → repair_project when budget remains
timeout       → build_project when budget remains
dependency    → failed
environment   → failed
unknown       → failed
```

## 9. Diagnostic Parsing and Sanitization

The Adapter parses common GCC/Clang diagnostics:

```text
file:line:column: severity: message
file:line: severity: message
```

It also parses project CMake diagnostics such as:

```text
CMake Error at path/CMakeLists.txt:line (command):
```

Only `warning` and `error` values enter `BuildDiagnostic.severity`; fatal compiler errors normalize to `error`. Parsing is best-effort and bounded. Duplicate diagnostics are removed while preserving occurrence order.

For a path that resolves lexically inside the project root, `BuildDiagnostic.file` uses normalized POSIX-style project-relative form. Project-external absolute paths are omitted from diagnostic fields. The parser does not follow diagnostic paths or read files.

Before summaries enter State, the Adapter:

1. strips ANSI control sequences;
2. normalizes newlines;
3. replaces the project root with a project placeholder before final relative-path presentation;
4. replaces recognized external absolute paths with a neutral placeholder;
5. selects failure-relevant context while preserving output order;
6. truncates deterministically to the configured character budget.

No prompt or model performs classification, path sanitization, or log selection. Complete build-log persistence is reserved for a future observability slice.

## 10. Testing Strategy

### Adapter unit tests

Default tests monkeypatch `subprocess.run` and command discovery. They verify:

- constructor validation;
- invalid project and missing launcher errors;
- strict manifest authorization before subprocess execution;
- project-root and manifest-scan symlink/junction rejection where the platform permits creating them;
- `IDF_COMPONENT_MANAGER=0` in default safe mode;
- normal manager environment under explicit authorization;
- fixed argument lists, validated `cwd`, and `shell=False`;
- `reconfigure` failure prevents `build`;
- successful two-phase execution;
- phase-specific timeouts;
- subprocess-start failure conversion;
- dependency, environment, linker, source, timeout, and unknown classification;
- GCC/Clang and CMake diagnostic extraction;
- Windows and POSIX path handling;
- external absolute-path removal;
- ANSI stripping, deduplication, and bounded summaries;
- stable sanitized `EspIdfError` values.

### Application and Graph tests

Fake-backed tests verify:

- dependency evidence routes to `failed`;
- existing source/linker repair and timeout retry behavior remains intact;
- Runner converts every `EspIdfError` category once at the workflow boundary;
- latest requirement, plan, trace, attempts, and prior evidence are preserved as applicable;
- the graph still contains the same seven business nodes.

### Real smoke test

One opt-in smoke test runs only when an explicit environment switch and test-project path are provided. It otherwise skips. It must never download dependencies by default and must use a deliberately dependency-free fixture project unless download authorization is separately explicit.

The required full-suite command remains:

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
```

## 11. Dependencies and Files

Runtime dependencies add `PyYAML>=6,<7` for safe manifest parsing. Hand-written YAML parsing is rejected because ESP-IDF manifests support nested mappings, lists, quoting, and conditional structures.

Expected production files include:

```text
src/luxar/adapters/espidf_cli.py
src/luxar/ports/espidf_errors.py
src/luxar/domain/evidence.py
src/luxar/domain/errors.py
src/luxar/application/routing.py
src/luxar/application/runner.py
src/luxar/bootstrap.py
pyproject.toml
```

Expected tests and Codex-owned documentation are specified by the later implementation plan. Exact file splits may be reduced if implementation shows that a private parser is clearer inside the Adapter module, but no subprocess logic may move into Domain, Ports, or LangGraph nodes.

## 12. Teaching Ownership

The user writes learning-critical implementation in small reviewed checkpoints, especially:

- the Adapter's `build` orchestration;
- dependency authorization branching;
- command-result conversion;
- diagnostic classification/parsing where educationally useful.

Codex writes all tests, Markdown, learning notes, diagrams, progress records, repetitive fixtures, and noneducational scaffolding. Codex explains the call chain and Python syntax before asking the user to write each core checkpoint.

## 13. Success Criteria

1. The real Adapter satisfies the unchanged `EspIdfPort` contract.
2. Default operation cannot invoke Component Manager for a project declaring managed dependencies.
3. Only explicit application configuration permits dependency resolution/download.
4. Commands run without a shell, with bounded time and bounded sanitized output.
5. Pre-command failures become truthful stable workflow errors; completed commands become truthful build evidence.
6. Source diagnostics include project-relative file, line, and column when available.
7. Dependency and environment failures never enter the source-repair loop.
8. The existing seven-node graph topology and Fake-backed behavior remain intact.
9. Default tests pass without a local ESP-IDF installation; real smoke coverage is opt-in.
