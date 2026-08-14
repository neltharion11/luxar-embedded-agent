# LUXAR Web UI and API Design

Status: approved by the learner on 2026-08-14

## 1. Goal

Preserve the original LUXAR user interface while replacing its legacy Agent
backend with a new presentation adapter over the verified LangGraph
application.

The first production slice must support this complete path:

```text
open the local Web UI
→ select an existing ESP-IDF project
→ submit one firmware task
→ receive safe progress events
→ receive the final allowlisted workflow result
```

The Web entrypoint and the existing CLI are peer presentation adapters. Both
call the same Bootstrap and Runner. The Web server must never invoke the CLI as
a subprocess.

## 2. Confirmed decisions

- Copy the original single-file HTML UI into the clean new repository.
- Preserve its visual design and basic chat interaction.
- Do not copy the original FastAPI routes, Agent loop, tool dispatcher, memory,
  model configuration, driver library, skill system, or serial-monitor backend.
- Add a new FastAPI adapter owned by the presentation layer.
- Use Server-Sent Events for safe workflow progress and the final result.
- Keep DeepSeek secrets exclusively in backend environment variables.
- Accept project names from the browser, never arbitrary project paths.
- Resolve names only beneath one configured projects root.
- Initially support existing direct-child ESP-IDF projects only.
- Mark unsupported UI surfaces as unavailable instead of returning fabricated
  data.
- Do not claim that the browser Stop button cancels backend execution until a
  real cancellation contract exists.
- Keep the current seven-node LangGraph topology unchanged.

## 3. Architecture

```text
Browser (original HTML/CSS/JS)
        ↓ HTTP + SSE
FastAPI presentation adapter
        ↓
Bootstrap + Runner
        ↓
seven-node LangGraph
        ↓
Ports
        ↓
DeepSeek / LocalWorkspace / ESP-IDF Adapters
```

The new files are expected to be:

```text
src/luxar/web.py                 FastAPI app factory and Web CLI entry
src/luxar/web_contracts.py       validated Web request/response models
src/luxar/web_projects.py        contained project discovery and resolution
src/luxar/web_streaming.py       Runner progress to SSE translation
src/luxar/ui/index.html          migrated and adapted original UI
tests/web/...                    Web presentation tests
```

Files may be combined if doing so makes the first slice clearer, but project
resolution and stream serialization must remain independently testable.

## 4. Project trust boundary

The server starts with an explicit absolute `projects_root`. The browser sees
only project names.

Allowed project selection:

```text
projects_root / project_name
```

The resolver must reject:

- empty names;
- absolute paths;
- drive-qualified names;
- `.` or `..` traversal;
- path separators;
- names resolving outside the configured root;
- symlink or Windows Junction project directories;
- missing directories;
- directories without a root `CMakeLists.txt`.

The first slice lists only direct children. It does not recursively expose the
host filesystem and does not provide a native directory picker.

## 5. HTTP contract

### `GET /`

Return the migrated UI.

### `GET /api/health`

Return fixed service readiness data without secrets or absolute paths:

```json
{"status":"ok","service":"luxar-langgraph"}
```

This does not prove DeepSeek or ESP-IDF availability.

### `GET /api/workspace/projects`

Return safe existing project descriptors:

```json
{
  "projects": [
    {"name":"blink","platform":"espidf"}
  ]
}
```

Names are sorted. Absolute paths are excluded.

### `POST /api/conversations/{project}`

Request body:

```json
{
  "message":"修复 GPIO 编译错误",
  "stream":true,
  "max_attempts":3,
  "allow_dependency_downloads":false
}
```

Rules:

- `message` is stripped and must remain non-empty;
- `stream` must be true in this slice;
- `max_attempts` is a bounded positive integer;
- dependency download permission defaults to false and reaches Bootstrap only
  from this explicit boolean;
- uploaded document content from the legacy UI is not accepted in this slice.

Response media type: `text/event-stream`.

### `GET /api/conversations/{project}`

Return an empty or process-local conversation view for UI compatibility. This
is not durable history. Durable conversation state must wait for the LangGraph
checkpoint persistence slice.

### `POST /api/conversations/{project}/reset`

Clear only process-local presentation history for the selected valid project.

## 6. SSE contract

Events are presentation data, not raw LangGraph State.

### `progress`

```json
{"stage":"build","message":"已完成第 1 次构建","attempts":1}
```

Only fields from `WorkflowProgress` are allowed.

### `result`

Use the same allowlisted business envelope as CLI JSON mode:

```json
{
  "status":"completed",
  "exit_code":0,
  "attempts":2,
  "requirement":{},
  "plan":{},
  "build_evidence":{},
  "repair_plan":{},
  "changed_files":[],
  "error":null,
  "trace":[]
}
```

The task text, Context, absolute project root, API key, raw exception, raw model
response, and unrestricted source content are forbidden.

### `error`

Only startup/presentation failures use a fixed safe error event:

```json
{"category":"startup","message":"运行配置无效，请检查服务端环境变量"}
```

Workflow failures are normal `result` events with `status="failed"`.

### `done`

The stream ends with `[DONE]`.

The backend may also emit the legacy `phase_changed` event during migration so
the preserved UI can update its existing status component. It must carry only
the same safe progress fields.

## 7. Execution and concurrency

`run_workflow()` is synchronous. The Web adapter runs it outside the async event
loop and passes a reporter that writes safe progress objects into a bounded
queue. The SSE generator reads that queue and emits events.

The first slice permits at most one active workflow per project. A concurrent
request for the same project returns HTTP 409. A server-wide concurrency bound
prevents unbounded DeepSeek/build workers.

Client disconnect does not yet cancel a running build. The UI must not describe
disconnect as backend cancellation. Real cancellation belongs to a later
application contract.

## 8. UI migration

Preserve:

- layout, colors, chat bubbles, Markdown rendering and result cards;
- project selector;
- progress/status presentation;
- modified-file names;
- bilingual shell where it does not depend on unsupported backend data.

Adapt:

- project loading to the new safe project-list endpoint;
- task submission to the new request schema;
- SSE handling to `progress`, `result`, `error`, and `done`;
- result cards to the new allowlisted State envelope;
- service status to `/api/health`.

Disable or clearly label unavailable:

- project creation/import/deletion;
- arbitrary file picker and document attachment;
- driver library;
- skill library;
- model configuration editing;
- flash, probe, serial monitor and hardware status;
- durable conversation history;
- true backend cancellation.

The UI must not silently call legacy endpoints.

## 9. Packaging and startup

Add Web dependencies with bounded major versions:

```text
fastapi
uvicorn
```

Use Starlette's streaming response unless a separate SSE package is genuinely
needed.

Expose a separate command:

```powershell
luxar-web --projects-root C:\projects
```

Defaults:

```text
host: 127.0.0.1
port: 8000
```

Binding a non-loopback host requires an explicit command-line value. No API key
is accepted from the browser.

## 10. Testing

Default tests remain offline and use Fake application dependencies.

Required coverage:

- project name/path containment and link rejection;
- deterministic project listing without absolute paths;
- request validation and dependency authorization propagation;
- exact safe SSE event order;
- success, clarification and failed State serialization;
- sanitized startup errors;
- same-project concurrency rejection;
- static UI delivery;
- no legacy endpoint references for enabled UI actions;
- no API key, Context, raw task, raw source or absolute root in responses;
- unchanged seven-node topology;
- complete existing regression suite.

## 11. Acceptance criteria

- The original visual UI opens from the new repository.
- A user can select an existing contained ESP-IDF project and run one task.
- Safe progress appears before the final result.
- The final result reflects the verified LangGraph State.
- The browser cannot choose a project outside the configured root.
- Dependency downloads remain disabled unless explicitly authorized.
- Unsupported original features are visible as unavailable or removed from
  active navigation.
- The Web server and CLI share Bootstrap and Runner rather than calling each
  other.
- Tests and static audits pass.

