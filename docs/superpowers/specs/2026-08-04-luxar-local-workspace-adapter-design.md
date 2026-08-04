# LUXAR LocalWorkspaceAdapter Design

**Date:** 2026-08-04

**Status:** Approved in teaching; pending implementation plan

**Repository:** `C:\tmp\luxar-langgraph`

## 1. Goal

Implement a production-shaped `LocalWorkspaceAdapter` for the existing `WorkspacePort`. It safely reads an ESP-IDF project's own text source files and applies complete-file repair replacements to existing files only, with deterministic limits and rollback on handled multi-file write failure.

## 2. Scope

This slice adds:

- a local filesystem Adapter implementing `WorkspacePort`;
- an explicit ESP-IDF source/configuration allowlist;
- project-root containment and symlink/junction rejection;
- configurable per-file and total byte budgets;
- strict UTF-8 text handling and binary rejection;
- existing-file-only complete replacements;
- staged same-directory temporary writes;
- best-effort rollback for handled multi-file failures;
- stable provider-independent `WorkspaceError` values;
- direct filesystem tests using pytest temporary directories;
- one integration path through the existing `repair_project` node.

This slice does not create projects, create or delete files, modify managed dependencies, run `idf.py`, download dependencies, add persistent filesystem journaling, or claim crash-safe transactions across multiple files.

## 3. Architecture Boundary

```text
repair_project node
  → WorkspacePort
  → LocalWorkspaceAdapter
  → validated ProjectFile snapshots
  → RepairPlanner
  → validated RepairPlan
  → LocalWorkspaceAdapter.apply_repair
  → existing project files
```

The Adapter owns filesystem access. `RepairPlanner` receives `ProjectFile` values and returns `RepairPlan`; it never receives a filesystem handle. Domain path validation remains the first safety layer, while resolved-path containment immediately before I/O is the second.

## 4. Constructor Policy

The production class is created as:

```python
LocalWorkspaceAdapter(
    max_file_bytes=256 * 1024,
    max_total_bytes=1024 * 1024,
)
```

Both values must be positive integers. Tests may inject smaller values. Limits count encoded UTF-8 bytes, not Python characters.

## 5. File Allowlist

Allowed source suffixes:

```text
.c .h .cc .cpp .hpp .s .S .cmake .ld .csv
```

Allowed exact filenames:

```text
CMakeLists.txt
Kconfig
Kconfig.projbuild
sdkconfig.defaults
idf_component.yml
project_include.cmake
```

Suffix matching is case-insensitive where practical, while exact ESP-IDF filenames remain explicitly listed. `sdkconfig` is not sent to the model in this slice because it is generated configuration and can be large. Dependency resolution belongs to the ESP-IDF tool boundary.

Excluded directories:

```text
.git
.vscode
.idea
build
build_*
managed_components
__pycache__
any directory whose name starts with a dot
```

Excluded directories are not traversed. Managed components are resolved and maintained by ESP-IDF Component Manager and are not model-editable project source.

## 6. Project Root and Path Safety

Before any read or write, the Adapter:

1. requires `project_path` to exist and be a directory;
2. resolves the project root with strict filesystem semantics;
3. rejects a root that is a symbolic link or Windows junction;
4. normalizes Domain paths to project-relative POSIX form;
5. joins each relative path beneath the resolved root;
6. resolves the existing target strictly;
7. proves containment using `Path.relative_to(resolved_root)`;
8. rejects symbolic links or Windows junctions in the target or any path component beneath the root.

The Adapter never trusts string-prefix comparisons such as `str(target).startswith(str(root))`, because sibling names can share prefixes. It never returns an absolute path in `WorkspaceError.message`.

## 7. Read Algorithm

`read_project_files(project_path) -> list[ProjectFile]` performs:

1. root validation;
2. recursive deterministic discovery without entering excluded directories;
3. symlink/junction rejection for discovered in-scope paths;
4. file allowlist filtering;
5. sorting by normalized project-relative path;
6. `stat`-based per-file byte limit check before reading;
7. byte reading followed by a second limit check against the actual byte length;
8. NUL-byte rejection and strict UTF-8 decoding;
9. cumulative actual-byte limit enforcement;
10. conversion to validated `ProjectFile` values.

The Adapter fails explicitly on an oversized in-scope file or total context. It does not truncate content, because a complete-file repair based on truncated input would be unsafe.

## 8. Apply Algorithm

`apply_repair(project_path, repair) -> list[str]` uses a validate-stage-commit-rollback flow.

### Validate all targets

For every `FileReplacement` before any write:

- the target path must pass the Domain path rules;
- the target must already exist and be a regular file;
- the target must be in the same allowlist used by reads;
- resolved containment must hold;
- no root-to-target component may be a symlink or junction;
- replacement UTF-8 bytes must not exceed the per-file limit;
- all replacement bytes together must not exceed the total limit.
- each original target is read as bytes and must also obey the per-file and
  aggregate limits before it can be retained for rollback.

No file is created or deleted.

### Stage all contents

- Save each target's original bytes in memory for rollback.
- Create a uniquely named temporary file in the target's own directory.
- Write replacement bytes, flush, and close the temporary file.
- Do not replace any target until every temporary file was staged successfully.

### Commit and rollback

- Recheck target containment and link safety immediately before replacement.
- Replace each target with its staged file using `os.replace`.
- If a handled replacement fails, restore every already-replaced target from its saved original bytes through same-directory temporary files and `os.replace`.
- Always remove remaining temporary files.
- If rollback itself fails, raise `WorkspaceError(category="rollback_failed")`; otherwise raise the original sanitized write error.

This provides logical rollback for ordinary Python/I/O failures. It does not claim atomicity across several files during process termination, machine crash, or power loss. Persistent journaling is outside this slice.

On success, return normalized relative paths in the same order as `repair.replacements`.

## 9. Stable Errors

Add `src/luxar/ports/workspace_errors.py` containing a
`WorkspaceError(RuntimeError)` contract with:

- `category`;
- sanitized `message`;
- `retryable`.

Allowed categories:

```text
invalid_project
unsafe_path
unsupported_file
file_too_large
context_too_large
invalid_encoding
io
rollback_failed
```

Messages are fixed application-owned descriptions. They do not include absolute project paths, raw operating-system exception text, temporary filenames, or file contents.

Retryability is stable rather than inferred from raw OS text: validation, safety,
size, encoding, and rollback failures are non-retryable; an ordinary `io` failure
is retryable.

Add `"workspace"` to `WorkflowError.category`. The centralized Runner will catch
`CapabilityError` and `WorkspaceError` at the same single workflow boundary. A
`WorkspaceError` becomes `WorkflowError(stage="repair", category="workspace")`
with its declared `retryable` value and an application-owned message/suggestion.
The conversion preserves the latest requirement, plan, and `BuildEvidence`.
The Graph topology and business node signatures remain unchanged.

## 10. Testing

Default tests perform real I/O only inside pytest-managed temporary directories. They cover:

- deterministic allowlisted reads;
- excluded-directory pruning;
- unsupported-file omission;
- UTF-8 decoding and binary/NUL rejection;
- per-file and total read limits;
- invalid or nonexistent project roots;
- symlink rejection where the platform permits symlink creation;
- Windows junction rejection where supported;
- absolute, traversal, and out-of-root rejection;
- existing-file-only enforcement;
- unsupported replacement rejection;
- replacement byte budgets;
- successful single- and multi-file complete replacement;
- cleanup of staging files;
- rollback when a later replacement fails;
- sanitized `WorkspaceError` messages;
- Runner conversion to failed State with evidence preservation;
- unchanged Fake-backed Graph behavior.

Tests that require OS symlink or junction privileges skip only when the platform cannot create that link type. Ordinary safety tests do not skip.

## 11. Dependency Preflight Decision for the Next Slice

Dependency validation belongs to `EspIdfCliAdapter`, not `LocalWorkspaceAdapter`. Different projects may declare built-in, registry, Git, local, versioned, target-conditional, or Kconfig-conditional dependencies.

The future build flow is:

```text
validate ESP-IDF environment and project structure
→ run idf.py reconfigure
→ resolve manifests and lock data
→ require dependencies to be available
→ run idf.py build only after successful preflight
```

The default policy is `allow_dependency_downloads=False`. Missing dependencies produce a stable `dependency` failure. Network resolution/download is permitted only when the caller explicitly sets `allow_dependency_downloads=True`. `managed_components` and `dependencies.lock` remain tool-owned and are never modified by the model Workspace Adapter.

## 12. Success Criteria

1. Only existing allowlisted ESP-IDF project files can be modified.
2. No resolved read or write target can leave the project root.
3. Symlinks and junctions cannot redirect in-scope I/O.
4. Source context and replacement content obey configurable byte budgets.
5. A handled multi-file failure restores previously replaced files or reports explicit rollback failure.
6. Workspace errors are stable, sanitized, and become explicit failed workflow State.
7. The existing LangGraph topology, Ports, and Fake-backed behavior remain intact.
