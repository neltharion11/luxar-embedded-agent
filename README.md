# LUXAR LangGraph

A clean, enterprise-shaped reconstruction of LUXAR using explicit domain models, Ports and Adapters, structured execution evidence, and LangGraph orchestration.

## Current milestone

Stages 0–15 are implementation-complete, and the release baseline uses three
explicit runtime boundaries: qualified Supervisor firmware by default,
dedicated project-inspection/knowledge workflows, and an explicit legacy
firmware rollback path. On top of that baseline the repository now ships the
conversation-first Continuous Agent V2 as the default Web entry, specialized
read/knowledge workflows, runtime governance (qualification, observation and
retirement gates), a dashboard-configurable model layer, and deterministic
ESP-IDF grounding during code generation and repair. Legacy removal is not
yet qualified because the persisted 30-day observation window has not elapsed.

The qualified Supervisor Agent covers the complete firmware pipeline:

```text
natural-language task
  → ProjectObjective + ChangeSet + preserved capabilities
  → project/resource/component inspection
  → hardware validation
  → dependency-aware AgentTaskGraph
  → transactional task execution with bounded repair
  → acceptance verification backed by tool evidence
  → build → explicit flash approval → flash → monitor
  → completed / blocked / clarification / sanitized failed State
```

LangGraph owns State transitions, Supervisor routing, bounded task retries,
interrupt-based approval, recovery, and streaming. Domain models own
validation, preservation rules, hardware constraints, and acceptance criteria.
Ports describe external capabilities. Runtime Context can inject deterministic
Fakes or the production DeepSeek, workspace, and ESP-IDF Adapters without
changing the Graph topology.

The default suite uses no real model call, user-project write, serial port,
or `idf.py`. Hardware smokes are explicitly opt-in
(`LUXAR_RUN_ESPIDF_SMOKE=1` + `LUXAR_ESP32_PORT`). A centralized application
error boundary converts model-side, workspace-side, and ESP-IDF capability
failures into sanitized State values while preserving structured objectives,
task progress, evidence, and diagnostics.

## Production entry chain

```text
firmware task
  → select_firmware_runtime(...)
  ├─ qualified/default Supervisor
  │    → build_deepseek_agent_runtime_context(...)
  │    → run_agent_workflow(...) / resume_agent_workflow(...)
  └─ explicit legacy rollback
       → build_deepseek_runtime_context(...)
       → run_workflow(...) / resume_workflow(...)

inspection or knowledge task
  → build_deepseek_specialized_runtime_context(...)
  → run_specialized_workflow(...) / resume_specialized_workflow(...)
```

Every Runner contains the application-level error boundary for its own
workflow contract. Business nodes remain provider and filesystem-implementation
independent. Specialized workflows do not construct firmware execution ports.

## Supervisor topology

```text
START → load_project_session → supervisor
  ├─ project_inspector ────────┐
  ├─ hardware_validator ───────┤
  ├─ architecture_planner ─────┤→ supervisor (bounded loop)
  ├─ task_executor ────────────┤
  ├─ acceptance_verifier ──────┘
  └─ answer / complete / fail / degrade → END
```

## Legacy rollback topology

The following graph remains only for the explicit one-release firmware
rollback path and pre-migration checkpoint compatibility:

```text
START → analyze_requirement
  ├─ incomplete → request_clarification → END
  └─ complete → create_plan → execute_next_step (plan cursor)
                                ├─ create_project → cursor
                                ├─ build_project → cursor
                                │    └─ source/linker → repair loop
                                ├─ flash_project → request_flash_approval
                                │    ├─ approved → flash → cursor
                                │    └─ rejected → failed
                                └─ flash monitor / monitor_project
                                     → analyze_device_logs
                                     ├─ healthy → completed
                                     └─ needs_repair → repair → build
                                          → re-flash → re-monitor (≤ 3)
```

The ESP-IDF Adapters validate projects and launchers, reject undeclared
network authority, run `idf.py` with bounded timeouts, sanitize output,
validate serial port names against platform patterns, and convert bounded
sanitized logs into evidence and diagnostics. Dependency downloads remain
forbidden by default and require explicit application-level authorization.
Flashing always requires human approval before the first flash of a run.

## Command-line entrypoint

After editable installation and activation of the configured environment:

```bat
luxar run --project C:\projects\blink --task "修复 ESP32 GPIO 工程" --port COM4
luxar ports
```

Omit `--task` in ordinary mode to enter it interactively. Flash approval is
interactive (`y`/`N`, default reject). Use `--json` only with an explicit task
for a stable machine-readable result; JSON mode requires `--approve-flash` to
pre-authorize flashing. Dependency downloads are forbidden unless
`--allow-dependency-downloads` is explicitly supplied. Model secrets remain
environment-backed and never appear as CLI arguments.

The Stage 10-qualified `supervisor` runtime is now the default for firmware
tasks. Set `LUXAR_AGENT_RUNTIME=legacy` for the one-release rollback path, or
set it explicitly to `supervisor`. Project inspection and knowledge tasks keep
their dedicated workflows; knowledge writes pause for explicit approval.
Device flashing follows
`build → approval → flash monitor → analyze_device_logs`; the production
adapter runs `idf.py -p PORT flash monitor` as one process so esptool and the
monitor never compete for the same serial port. CLI JSON mode only
pre-authorizes the `device.flash` operation when `--approve-flash` is present.

`GET /api/runtime` reports the selected firmware runtime, why it was selected,
the legacy deprecation/support window, and the evidence gates that still block
legacy removal. Dedicated project-inspection and knowledge workflows have
their own State, Graph, narrow Bootstrap, checkpoint Runner, result contract,
and `workflow_family`; they do not construct firmware execution ports and are
not counted as firmware rollback usage.

`GET /api/runtime/audit` performs a read-only retirement audit over persisted
run metadata, pending approvals, and the local SQLite checkpoint inventory.
The audit requires durable storage, a 30-day persisted observation baseline,
zero legacy firmware runs, zero ambiguous pre-migration records, and no active
recovery dependency before it can qualify as release evidence. It never reads
checkpoint payloads or exposes thread identifiers.

## ESP-IDF 工具链单一权威与 SDK 接地

编译、烧录与示例检索共用同一个权威解析（configured → environment →
installer → search），由 `EspIdfToolchainManager` 解析一次：在 Dashboard
选定的 ESP-IDF 根目录持久化到 projects 根目录的 `.luxar/toolchain.json`，
Web 与 CLI 的构建路径都遵守这个 pin，避免"网页选了一个版本、编译却用另
一个"的分叉。每次构建的 `BuildEvidence` 记录实际使用的 `idf_path` 与
`idf_version`（从 `tools/cmake/version.cmake` 读取，无子进程），下游修复
因此总能对齐权威源。

Supervisor 的修复路径在构建失败时用只读 SDK 探测把诊断接地到已安装的
ESP-IDF（不启动进程、不写文件）：

- 缺失头文件（`fatal error: X.h: No such file`）：在 components include
  树中判定存在性，并给出最接近的替代头文件（例如 ESP-IDF v6 移除了
  `driver/i2c.h`，探测会给出 `driver/i2c_master.h`）。
- API 改名/移除/弃用（`implicit declaration of function`、`is deprecated`、
  `unknown type name`、`no member named`）：在 `docs/en/migration-guides`
  中按符号名检索原文摘录，注入修复反馈。

两类提示都来自本次编译真正使用的 IDF 树，版本正确是构造性的；编译器
仍是最终裁判。

## Web gateway

```bat
luxar web --projects-root F:\LUXAR\projects --serial-port COM4 --target esp32
```

Project root, serial port, and target chip are all selectable **per task in
the page**: the project root switches among the server's configured roots
(`--projects-root` may be repeated), the serial port is picked from the list
the server discovers live (`GET /api/devices/ports`), and the chip comes from
a fixed dropdown. Server-side values only act as defaults when the page
leaves a field unset. The browser still never submits arbitrary paths, port
names, or chip strings: roots must be configured at startup, ports must match
the platform pattern and the discovered list, and chips are strict lowercase
identifiers. Flash approvals surface as an in-page approval card
(`批准烧录` / `拒绝`) backed by `POST /api/conversations/{project}/approval`.

前端错误显式呈现：工具执行失败/被拒绝、模型决策失败、运行边界异常都会
在对话流中渲染为**结构化错误卡片**——分类（模型/工具/策略/参数校验/服务/
超时等）、稳定错误码（如 `service`、`display_selfcheck_mismatch`）、
是否可重试，以及可展开的 `details` 排查依据；历史会话恢复时同样重建这些
卡片。错误不再只藏在后台日志里。

### Continuous Agent V2 (default Web entry)

Web conversations route through the conversation-first LangGraph Agent by
default. Set `LUXAR_CONTINUOUS_AGENT_V2=false` only as an emergency rollback to
the legacy / Supervisor entry path. The former project allowlist and shadow
rollout variables are ignored; routing is deliberately consistent across all
projects. `/api/runtime` and the conversation payload expose the resolved mode.

V2 keeps one durable Agent Session per active project conversation and creates
an independent Turn/SSE stream for every user message. The browser sends a
stable `session_id` plus a unique `client_turn_id`; duplicate client requests
replay the original Turn instead of repeating tools. The top model can answer,
request genuinely missing input, call typed tools, or delegate a complex
multi-file change to the existing Supervisor through `project.change`.
Workspace writes and device actions remain behind deterministic validation,
approval, and a persistent idempotency ledger. Waiting input and approvals can
resume from SQLite after a service restart.

The response headers expose `X-LUXAR-Session-ID` and `X-LUXAR-Turn-ID`;
`X-LUXAR-Thread-ID` remains the compatible Turn stream identifier. Session
state is available from `GET /api/conversations/{project}/session`, and a new
Session can be created with `POST /api/conversations/{project}/sessions`.
While a Turn is running, the browser can enqueue a follow-up instruction with
`POST /api/conversations/{project}/steer` or request a safe-boundary stop with
`POST /api/conversations/{project}/cancel`. Steering messages are durable and
idempotent; cancellation is checked before tool and domain-workflow side
effects, and it cancels only the current Turn/objective while leaving the
Session available for the next message.

All entry points share one command shape:

```bat
luxar        :: bare invocation starts the Web gateway with env/default config
luxar run    --project DIR --task "..." [--port COM4] [--target esp32] [--json]
luxar ports
luxar web    --projects-root DIR [--serial-port COM4] [--target esp32]
luxar setup
luxar storage health
luxar storage runtime-audit
luxar storage runtime-migration-plan
```

Bare `luxar` equals `luxar web` with values from `LUXAR_PROJECTS_ROOT`
(multiple roots separated by the platform path separator),
`LUXAR_SERIAL_PORT`, `LUXAR_TARGET_CHIP`, and `LUXAR_WEB_PORT`, falling
back to `./projects` and port 8000.

(`luxar-web` remains as a compatible alias for `luxar web`.)

## 显示屏字模取模工具（font.extract / font.export）

LLM 手写显示屏驱动里的字体位图几乎必然出错（字形、位序、行列顺序都对不
上）。LUXAR 的持续 Agent（Web 默认入口）内置了确定性取模工具，把字形
光栅化和位打包交给代码完成，模型只负责描述需求，不再手写任何位图字节：

- `font.extract`：只读。按控制器的内存布局生成字模 C 代码并返回头文件
  内容（不写文件），供模型预览或嵌入小字体内联数组。
- `font.export`：写文件（需审批）。把生成的字模头文件直接写入当前工程
  指定路径（限 `.h/.hpp/.c/.cpp`，必须位于工程内），避免模型转抄长十六
  进制数组时出错。

参数要点（与 PCtoLCD 等取模软件对应）：

- `text`：要取模的字符，自动去重并按首次出现排序；纯 ASCII 默认 8x16，
  含中文默认 16x16（可显式指定 `width`/`height`）。
- `font`：默认 `msyhbd`（微软雅黑粗体，笔画粗、16px 下 'e'/'w' 等小写
  字母清晰，最接近嵌入式点阵字体观感）；其他可选
  `msyh`(微软雅黑)/`arialbd`(Arial 粗体)/`consolab`(等宽粗体)/`simhei`(黑体)/
  `simsun`(宋体)/`consola`/`arial`/`cascadia_mono`/`noto_sans_sc`，或字体
  文件绝对路径（限 Windows 字体目录或工程内）。**内置 U8g2 点阵字体**
  （MIT，源自 olikraus/u8g2 的 BDF 源，纯 ASCII 32-127、等宽、像素级
  清晰、嵌入式标准观感，不依赖系统字体）：`u8g2_5x7`/`u8g2_6x10`/
  `u8g2_8x13`/`u8g2_10x20`——含中文时不可用，需改用 TTF 字体。
- `controller`：控制器预设，如 `ssd1306`/`sh1106`=逐列(纵向)取模、
  `pcd8544`/`st7789`/`ili9341`/`hd44780`=逐行(横向)取模；也可用
  `scan=row|column`、`bit_order=msb|lsb`、`invert=true`(阴码) 单独覆盖。
- `ascii_half_width=true`：混合中英文字库。ASCII 按字体实际字宽（比例
  advance）取模并左对齐——'l' 窄、'w' 宽，拼接成单词间距均匀、字形不被
  压扁（建议配合 msyh 等比例拉丁字体）；汉字取全宽。ASCII 字形垂直方向
  **基线对齐**（所有字母底边落在同一条基线上，'g'/'p' 等降部向下伸），
  中文方块字垂直居中。输出 `GLYPH_WIDTHS`/`GLYPH_OFFSETS` 表，驱动按
  宽度累加即可在 128px 屏上正确居中混排（如 msyh 下 "helloworld" 10 字符
  80px、x0=24；"你好世界" 64px、x0=32）。
- 输出头文件包含每个字形的 ASCII 预览注释、Unicode 码点（含中文时附
  `CODEPOINTS` 索引表）与取模参数，便于驱动作者核对并实现查表。

取模由 PyMuPDF 确定性光栅化完成（已有硬依赖，无需新增依赖）：同一字体
文件 + 同一参数必然产生同一输出，并以 `font.extract` 的 `c_code` 或写入
的头文件作为构建证据。

**模型强制使用规则**：持续 Agent 的决策提示（`continuous_agent_step.py`
的 system prompt）规定——涉及显示屏字体/字模/显示字符串的驱动任务时，
若工具目录存在 `font.extract`/`font.export`，必须先调用它们生成字模，
禁止手写任何位图字节数组；用户未给出具体字符时先 `ask_user` 询问，不得
猜测；委托领域工作流前也要先由顶层生成字模文件。这与 `driver.search` 的
强制复用规则同级，从机制上杜绝模型手写字模导致的乱码。

### 设备侧显示自检（display.verify / display.selfcheck_template）

自动验证"驱动把正确的字模字节写到了正确的显存位置"，不再依赖模型自报：

1. `font.export` 生成的字模头文件自带**每字形 crc32** 与整表
   `data_crc32`/`data_sha256` 锚点（头文件注释中），任何手改字模文件的
   字节都会被锚点校验当场识破。
2. `display.selfcheck_template` 输出零依赖的 `display_selfcheck.h/.c`
   （zlib 标准 CRC32 表 + `FONT_CHECK <name> <crc32>` 打印函数），写入
   工程后在清屏→绘制→刷新显存后调用一次，经 UART 回传整帧 CRC。
3. `display.verify` 解析工程内字模头文件，按 `lines=[{text,x,y},...]`
   多行布局重建页寻址预期帧（与固件同一字节布局），与设备回传 CRC 对比：
   一致即通过（逻辑层验证），不一致则失败并给出预期/实际 CRC 与每字形
   锚点，供模型定位修复。

该闭环不依赖摄像头或屏内 RAM 回读，仅用显存字节流校验，任何"手写字模"
导致的位序/行列/宽度错误都会在第一次 `display.verify` 中被确定性抓住。

## Embedded SQLite durability and LanceDB knowledge

LUXAR now uses an embedded local storage profile by default. SQLite stores
workflow runs, conversations, approvals, structured project memory, and
LangGraph `SqliteSaver` checkpoints. LanceDB stores knowledge documents,
chunks, metadata, and vectors. No Docker or database service is required.

See [STORAGE.md](STORAGE.md) for storage ownership, recovery, backup, and the
optional PostgreSQL compatibility profile.

```bat
copy .env.example .env
luxar storage health
luxar
```

`GET /api/health/database` reports `database=sqlite` and `durable=true`. Project
memories are structured JSON records exposed through
`GET|PUT /api/projects/{project}/memories`; only explicit device selections
are learned automatically. Raw conversations and model guesses are not
silently promoted to long-term memory.

`luxar storage runtime-audit` prints the same privacy-safe retirement audit as
the Web endpoint. `luxar storage runtime-migration-plan` performs a read-only
dry run over pre-`workflow_family` SQLite records. It reports only aggregate
deterministic candidates and ambiguous counts; it does not modify metadata or
print task text/thread identifiers.

The external knowledge API uses LanceDB vector search plus bounded lexical
reranking:

```text
POST /api/projects/{project}/knowledge/documents
POST /api/projects/{project}/knowledge/search
GET  /api/projects/{project}/knowledge/documents
GET  /api/projects/{project}/knowledge/documents/{document_id}
DELETE /api/projects/{project}/knowledge/documents/{document_id}
POST /api/projects/{project}/knowledge/import-pdf
```

Set `LUXAR_EMBEDDING_API_KEY` (and optionally base URL/model/dimensions) to an
independent OpenAI-compatible embedding endpoint. Retrieved chunks carry
their title and source URI and enter requirement analysis as untrusted
reference context, not as executable instructions.

PDF imports are segmented by outline chapters first, with heading inference as
a fallback and bounded character subparts only for oversized chapters, so a
large manual is not silently truncated at one model-context limit. Text and
page-level visual metadata are always indexed. To interpret schematic or
drawing-only pages, configure `LUXAR_DOCUMENT_VISION_API_KEY`,
`LUXAR_DOCUMENT_VISION_BASE_URL`, and `LUXAR_DOCUMENT_VISION_MODEL` for an
OpenAI-compatible vision model.

## 模型配置

模型端点由 Dashboard 的模型设置面板保存到 projects 根目录的
`.luxar/model-config.json`（provider / base_url / model / timeout /
context window），密钥只落盘、不出现在读取响应中；未保存时回退到
`LUXAR_LLM_PROVIDER`、`LUXAR_LLM_BASE_URL`、`LUXAR_LLM_MODEL`、
`LUXAR_LLM_API_KEY` 等环境变量。交互式 Agent 默认关闭 DeepSeek thinking
以缩短首条自然语言 commentary 的等待时间；可用
`LUXAR_LLM_THINKING_ENABLED=true` 与 `LUXAR_LLM_THINKING_EFFORT` 显式开启。
provider 支持 deepseek / openai / local
（任意 OpenAI 兼容端点）。PDF 视觉解析支持 inherit / separate / python
三种模式；知识库 embedding 支持 local_hash（离线）与 api 两种模式。

## Zero-config startup for new machines

One command covers both fresh machines and everyday startup:

```bat
powershell -ExecutionPolicy Bypass -File start.ps1
```

On a fresh clone, `start.ps1` performs the **complete** initialization in one
run: finds a Python 3.12 (offers a winget install when missing), creates the
`.venv`, installs the package with dev extras (including pyserial), generates
the gitignored `.env` (`DEEPSEEK_API_KEY`, project root, optional
`LUXAR_SERIAL_PORT`, `LUXAR_TARGET_CHIP`, `LUXAR_WEB_PORT`), **writes
`.venv\Scripts` into the user PATH automatically**, and detects an existing
ESP-IDF. It then starts the gateway immediately.

After that single run, a **new terminal** can use the CLI directly:

```bat
luxar                 :: start the gateway
luxar run --project ... :: run a firmware task
luxar ports           :: list serial ports
luxar setup           :: re-check the environment (instant when ready)
```

When an environment already exists (complete `.venv`, `LUXAR_PYTHON`, or any
Python with luxar importable), `start.ps1` skips setup entirely and starts the
gateway right away. `scripts\setup.ps1` remains available standalone
(`-Force` reinstall, `-NoPathEdit` skips the PATH write); `scripts\run-web.ps1`
is a thin alias of `start.ps1`.

Hardware note: LUXAR checks the ESP-IDF command when the Web gateway starts.
It searches `IDF_PATH`/`IDF_PYTHON_ENV_PATH`, Espressif Installation Manager
records, `idf.py` on `PATH`, common standard install directories, and the path
previously saved from the Dashboard. The Web UI shows a global warning when no
usable environment is found. The Dashboard can open a native folder picker for
an ESP-IDF root; that choice is saved under the first project root at
`.luxar/toolchain.json` and reused on later starts. A directory is considered
usable only when its `tools/idf.py --version` command actually succeeds, so an
incomplete ESP-IDF Python environment remains marked unavailable until it is
repaired.

## Development environment

```bat
C:\Users\41562\.conda\envs\luxar-learning\python.exe -m pip install -e ".[dev]"
C:\Users\41562\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
```

## Optional real DeepSeek smoke

The default command skips the real API smoke test. To explicitly spend one
minimal DeepSeek request, provide `DEEPSEEK_API_KEY`, set
`LUXAR_RUN_DEEPSEEK_SMOKE=1`, and run:

```bat
C:\Users\41562\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/smoke/test_deepseek_requirement_parser.py
```

Never place the API key in source code, Markdown, State, prompts, or Git.

## Optional real ESP-IDF smoke

The default suite also skips the real ESP-IDF build and device operations.
Activate the ESP-IDF v6.0.2 environment, explicitly set
`LUXAR_RUN_ESPIDF_SMOKE=1` and `LUXAR_ESP32_PORT`, and run:

```bat
C:\Users\41562\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/smoke/test_espidf_cli.py
C:\Users\41562\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/smoke/test_device_smoke.py
```

The device smoke creates a dependency-free project under pytest's temporary
directory, builds it, flashes the connected board (replacing its current
firmware), and immediately captures the monitor window in the same `idf.py`
process. It never installs ESP-IDF,
automatically downloads components, or uses a user project.
