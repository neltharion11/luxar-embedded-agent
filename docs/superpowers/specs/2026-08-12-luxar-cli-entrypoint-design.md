# LUXAR CLI Entrypoint Design

**Date:** 2026-08-12

**Status:** Approved design, pending written-spec review

**Scope:** A production-shaped command-line entrypoint for the existing DeepSeek + LangGraph + Workspace + ESP-IDF workflow

## 1. Goal

Provide one installable `luxar run` command that accepts an explicit ESP-IDF
project path, obtains a firmware task from an argument or an interactive prompt,
constructs the existing production Adapters, executes the existing seven-node
LangGraph workflow, reports safe stage progress, prints either a human-readable
Chinese result or stable JSON, and returns a meaningful process exit code.

This slice exposes the already completed application; it does not redesign the
Graph, add persistence, add human approval, create new projects, or add a Web
API.

## 2. Confirmed Product Decisions

- Implement a CLI only. Do not add a separate public Python Application API in
  this slice.
- Use the Python standard library `argparse`; do not add Typer or Rich.
- `--project` is required. Never default to the current directory and never
  interactively ask for a project path.
- `--task` is optional only in ordinary human-readable mode. When omitted, ask
  for it with `input()`.
- `--json` requires `--task`; JSON mode never waits for interactive input.
- Dependency downloads remain forbidden by default and require the explicit
  `--allow-dependency-downloads` flag.
- Ordinary mode shows safe Chinese stage progress and a final Chinese summary.
- JSON mode emits no progress and writes exactly one final JSON document to
  stdout.
- Exit codes are `0` for completed, `2` for arguments/configuration, `3` for
  needs clarification, `4` for workflow failure, and `130` for Ctrl+C.
- Model endpoint, model names, and API key remain environment configuration.
  Secrets are not accepted as command-line arguments.

## 3. Non-Goals

This slice does not add:

- a Web UI, HTTP API, TUI, or GUI;
- LangGraph checkpoint persistence or resumed conversations;
- human approval with `interrupt()`;
- automatic project creation;
- component-download prompting or automatic escalation of download authority;
- flashing, serial monitoring, Git operations, or deployment;
- full raw build-log persistence;
- model, endpoint, or API-key command flags;
- changes to the seven-node Graph topology, Domain contracts, or Port method
  signatures.

## 4. User Interface

Register the console script in `pyproject.toml`:

```toml
[project.scripts]
luxar = "luxar.cli:main"
```

Primary usage:

```powershell
luxar run --project C:\projects\blink --task "修复 ESP32 GPIO 工程"
```

Interactive task input:

```powershell
luxar run --project C:\projects\blink
请输入固件需求：
```

Machine-readable usage:

```powershell
luxar run --project C:\projects\blink --task "修复 ESP32 GPIO 工程" --json
```

Explicit dependency-download authorization:

```powershell
luxar run --project C:\projects\blink --task "构建工程" --allow-dependency-downloads
```

### 4.1 Arguments

```text
run
--project PATH                    required
--task TEXT                       optional only outside --json
--max-attempts INTEGER            default 3; must be a positive integer
--allow-dependency-downloads      default false
--json                            machine-readable final result
```

There is exactly one subcommand, `run`. Keeping the subcommand makes future
commands such as `inspect` or `resume` possible without redesigning invocation,
but those commands are not implemented now.

### 4.2 Two-Level Project Validation

The CLI performs only presentation-boundary validation:

- the supplied path is non-empty;
- it exists;
- it is a directory.

Failure here is an argument error with exit code `2`. The message may repeat the
path supplied by the user because it came directly from that same CLI invocation,
but it must not append OS exception text or resolved machine paths.

The `EspIdfCliAdapter` remains authoritative for security and engineering
validation: symlink/Junction rejection, strict resolution, root
`CMakeLists.txt`, launcher availability, manifests, byte limits, encoding,
YAML, and dependency authorization. Those failures happen during the workflow
and produce exit code `4` through the existing Runner boundary.

### 4.3 Task Validation

- If `--task` is provided, strip surrounding whitespace and reject an empty
  result with exit code `2`.
- If ordinary mode omits `--task`, call `input("请输入固件需求：")`, strip the
  result, and reject an empty result with exit code `2`.
- If `--json` omits `--task`, report an argument error immediately and never
  call `input()`.

## 5. Architecture

### 5.1 Files

Create:

```text
src/luxar/cli.py
tests/test_cli.py
docs/learning/10-cli-entrypoint.md
```

Modify:

```text
src/luxar/application/runner.py
tests/application/test_runner.py
pyproject.toml
README.md
docs/learning/00-LUXAR-Agent-复习总览.md
docs/learning/PROGRESS.md
```

The CLI is a presentation adapter. It may import Bootstrap, Runner, State, and
Domain serialization helpers, but it must not import LangGraph graph builders,
DeepSeek SDK classes, `subprocess`, YAML, or filesystem repair internals.

### 5.2 Dependency Flow

```text
terminal user / script
  → luxar.cli
  → build_deepseek_runtime_context(...)
  → RuntimeContext with production Adapters
  → run_workflow(..., progress_reporter=...)
  → existing compiled seven-node LangGraph
  → final WorkflowState
  → CLI formatter
  → Chinese stdout or JSON stdout + process exit code
```

The CLI never calls `build_graph().stream()` directly. The Runner remains the
single graph execution and capability-error boundary.

## 6. Safe Progress Reporting

### 6.1 Application Contract

Add an application-owned immutable value object and callback type in
`application/runner.py` or a focused adjacent application module if the plan
shows that separation is clearer:

```python
@dataclass(frozen=True)
class WorkflowProgress:
    stage: Literal[
        "requirement",
        "planning",
        "build",
        "repair",
        "clarification",
        "completed",
        "failed",
    ]
    message: str
    attempts: int


ProgressReporter = Callable[[WorkflowProgress], None]
```

`message` must come from an application-owned fixed mapping. A progress value
must never contain the full State, task text, Prompt, requirement details,
source, project path, API key, build summaries, diagnostics, exception message,
or model response.

### 6.2 Runner Signature

Extend the existing entrypoint compatibly:

```python
def run_workflow(
    *,
    initial_state: WorkflowState,
    context: RuntimeContext,
    progress_reporter: ProgressReporter | None = None,
) -> WorkflowState:
    ...
```

Existing callers omit the new argument and retain identical behavior.

### 6.3 Event Derivation

The Runner already consumes `stream_mode="values"`. For each successful
snapshot, derive the completed node from the final entry in `trace`. Ignore an
initial snapshot with no new trace entry and suppress duplicate trace lengths.

Map node names to safe progress values:

```text
analyze_requirement   → requirement / 需求分析完成
create_plan           → planning / 执行计划已生成
build_project         → build / 已完成第 N 次构建
repair_project        → repair / 已应用受限制的源码修复
request_clarification → clarification / 需要补充需求信息
completed             → completed / 工作流执行成功
failed                → failed / 工作流执行失败
```

`attempts` is copied as an integer only. It is `0` for stages before a completed
build attempt.

If an exception is caught before the graph's `failed` node runs, the Runner
creates the same safe `WorkflowProgress(stage="failed", ...)` after forming the
failed State. It reports exactly one failed event.

The reporter is application code supplied by the caller. Exceptions raised by
the reporter are not converted into `WorkflowError` and are not swallowed by a
broad catch; an output failure or programming bug must remain visible rather
than being misreported as a model, workspace, or ESP-IDF failure.

## 7. CLI Execution

Implement a conventional testable entrypoint:

```python
def main(argv: Sequence[str] | None = None) -> int:
    ...
```

The console script calls `main()` through setuptools. Tests can pass an explicit
argument list without modifying `sys.argv`.

Execution order:

1. Build and run the `ArgumentParser`.
2. Validate `--project`, `--task`, `--json`, and positive `--max-attempts`.
3. In ordinary mode only, obtain a missing task through `input()`.
4. Construct production `RuntimeContext` with:
   - the explicit project path;
   - `allow_dependency_downloads` from the explicit flag;
   - all DeepSeek settings from the existing environment-based settings.
5. Build the initial State:

```python
WorkflowState(
    task_text=task,
    attempts=0,
    max_attempts=max_attempts,
    trace=[],
)
```

6. Call the existing Runner, supplying a stderr progress reporter only in
   ordinary mode.
7. Format the final State.
8. Return the exit code derived from final status.

## 8. Output Contract

### 8.1 Stream Separation

Ordinary mode:

```text
stderr → safe stage progress
stdout → one final Chinese summary
```

JSON mode:

```text
stdout → exactly one final JSON document
stderr → argument/configuration errors only
```

A business `failed` State is still a successfully serialized machine result, so
JSON is written to stdout and the process returns `4`.

### 8.2 Ordinary Progress

Example:

```text
[需求] 需求分析完成
[计划] 执行计划已生成
[构建] 已完成第 1 次构建
[修复] 已应用受限制的源码修复
[构建] 已完成第 2 次构建
[完成] 工作流执行成功
```

Do not use a fixed total step count because repair and retry loops make the
number of stages dynamic.

### 8.3 Ordinary Final Summaries

Completed:

```text
LUXAR 执行成功
状态：completed
构建次数：2
修改文件：
  - main/main.c
最终命令：idf.py build
返回码：0
```

Needs clarification:

```text
LUXAR 需要更多信息
状态：needs_clarification
缺少字段：
  - gpio
```

Failed:

```text
LUXAR 执行失败
状态：failed
阶段：build
类别：dependency
原因：项目依赖需要显式授权后才能解析
建议：请确认依赖来源后显式允许依赖下载
```

Optional sections are omitted in ordinary output when their source field is
absent. Do not print raw task text, Prompt, source, API key, SDK exception,
project absolute path, or unsanitized log.

### 8.4 Stable JSON Envelope

JSON mode emits UTF-8 JSON with `ensure_ascii=False` and a stable top-level
shape:

```json
{
  "status": "completed",
  "exit_code": 0,
  "attempts": 2,
  "requirement": {},
  "plan": {},
  "build_evidence": {},
  "repair_plan": {},
  "changed_files": [],
  "error": null,
  "trace": []
}
```

Rules:

- `status` is the final workflow status.
- `exit_code` duplicates the process classification for scripts that retain only
  the document.
- Pydantic values use `model_dump(mode="json")`.
- Absent optional object values are `null`, not `{}`. The example `{}` means an
  object is present; the implementation must not fabricate empty Domain objects.
- `changed_files` and `trace` default to empty lists.
- Do not include `task_text`, project path, Runtime Context, settings, clients,
  API key, Prompt, raw exception, or full unbounded logs.
- `BuildEvidence` may include its already bounded and sanitized summaries and
  diagnostics because those fields passed the existing Adapter boundary.
- Emit one terminating newline after the JSON document.

## 9. Exit Codes and Startup Errors

```text
0   completed
2   argument or startup configuration error
3   needs_clarification
4   workflow failed
130 interrupted by Ctrl+C
```

Argument errors include missing required arguments, unknown options, invalid
positive integers, nonexistent/non-directory project input, empty task, and the
`--json`/missing-task conflict.

Startup configuration errors include missing or invalid environment-backed
DeepSeek settings before the workflow begins. Convert only known Pydantic
settings validation and expected constructor `ValueError` failures to a fixed
safe CLI message. Never print `repr(error)` or `str(error)`, because validation
details could include environment input. Do not catch broad `Exception`.

Existing stable capability failures during Graph execution are handled by the
Runner and become a final failed State with exit code `4`.

Handle `KeyboardInterrupt` around interactive input, composition, and execution:

```text
stderr: 操作已取消
exit: 130
```

## 10. Testing Strategy

### 10.1 CLI Unit Tests

Use `main([...])`, pytest capture fixtures, monkeypatch, and deterministic final
States. Do not call real DeepSeek or `idf.py`.

Cover:

- help and subcommand parsing;
- required `--project`;
- nonexistent and non-directory project arguments;
- positive `--max-attempts` and rejection of zero/negative values;
- ordinary mode calls `input()` only when `--task` is absent;
- whitespace-only task rejection;
- JSON mode without `--task` returns `2` and never calls `input()`;
- explicit dependency flag is passed to Bootstrap and is false otherwise;
- initial State contains task, attempts `0`, configured max attempts, and empty
  trace;
- ordinary progress appears only on stderr;
- ordinary completed, clarification, and failed summaries;
- JSON stdout parses as exactly one document with the stable envelope;
- JSON mode does not install a progress reporter;
- status-to-exit-code mapping for completed/clarification/failed;
- known settings error maps to fixed safe stderr and exit `2`;
- `KeyboardInterrupt` maps to fixed stderr and exit `130`;
- output contains no injected sensitive error/configuration marker.

For argparse failures, either test the parser contract through `SystemExit(2)`
or provide a small parser subclass that converts parser exits to integer returns;
the implementation plan must choose one approach consistently. The installed
console command must still follow standard argparse help and usage behavior.

### 10.2 Runner Tests

Cover:

- omitted reporter preserves all existing behavior;
- successful requirement, plan, build, repair, and terminal nodes emit ordered
  fixed progress events;
- initial snapshots do not emit an event;
- attempts reflect only completed build nodes;
- a caught Port exception emits exactly one failed event;
- progress objects expose only `stage`, `message`, and `attempts`;
- no task text, project path, requirement, plan, evidence, source, Prompt, logs,
  or exception message is passed to the reporter;
- reporter exceptions propagate and are not normalized as workflow failures.

### 10.3 Regression and Structural Checks

Run the required complete suite:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
```

Also verify:

- the seven-node Graph topology is unchanged;
- CLI does not import `build_graph`, `subprocess`, `yaml`, or OpenAI SDK classes;
- the API key has no CLI argument and is not serialized;
- `allow_dependency_downloads` only comes from the explicit CLI flag into
  Bootstrap;
- JSON mode has no progress text on stdout;
- `.vscode/` remains untracked and uncommitted.

## 11. Learning Deliverable

Codex completes the approved code, tests, scaffolding, and Markdown first. After
verification, create `docs/learning/10-cli-entrypoint.md` and teach backward from
the finished implementation. The chapter must explain:

- CLI, shell, argv, parser, subcommand, flag, option, stdin/stdout/stderr, exit
  code, callback, serialization, and presentation adapter;
- how setuptools maps the `luxar` command to `luxar.cli:main`;
- why CLI arguments are not LangGraph State until explicitly converted;
- why progress contains safe events instead of State snapshots;
- how ordinary output differs from JSON automation output;
- what argparse and CLI tests actually execute;
- the full chain from terminal command to final process exit code.

All explanatory Markdown remains Codex-owned.

## 12. Acceptance Criteria

1. Editable installation exposes `luxar run` through standard project scripts.
2. `--project` is always explicit; no current-directory fallback exists.
3. Ordinary mode can interactively obtain a missing task; JSON mode cannot.
4. Default dependency downloads remain forbidden; only the explicit flag grants
   authority through Bootstrap.
5. DeepSeek secrets and provider configuration remain environment-backed and do
   not appear in process arguments or output.
6. The CLI calls the existing Runner, never the Graph directly.
7. The Runner remains the only capability-error boundary and accepts an optional
   safe progress callback without changing existing callers.
8. Progress events contain only fixed stage, fixed message, and attempt count.
9. Ordinary progress uses stderr; final human output uses stdout.
10. JSON mode writes one stable document to stdout and no progress.
11. Exit codes are exactly 0/2/3/4/130 as designed.
12. Unknown programming errors are not hidden by a broad catch.
13. The seven-node topology, Domain invariants, Port contracts, and existing
    Adapter security policies remain unchanged.
14. The complete required pytest command passes, static boundary searches pass,
    and `.vscode/` remains outside Git.
