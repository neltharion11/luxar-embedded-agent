# Luxar Agent Manual v1.0

## 1. Project Layout

```
<project-root>/
├── config/luxar.yaml               # Central configuration
├── soul.md                         # Agent identity & red lines
├── agent.md                        # This manual
├── workspace/
│   ├── projects/<name>/            # Individual embedded projects
│   │   ├── App/                    # Agent-managed code (safe to modify)
│   │   ├── Core/                   # CubeMX generated (DO NOT TOUCH)
│   │   ├── Drivers/                # CubeMX HAL (DO NOT TOUCH)
│   │   ├── build/                  # CMake build artifacts
│   │   ├── cmake/                  # CMake toolchain & linker scripts
│   │   ├── CMakeLists.txt          # Agent-managed build file
│   │   ├── logs/                   # Agent activity logs
│   │   └── .agent_backups/         # Auto-generated snapshots
│   └── driver_library/             # Global driver repository (SQLite-backed)
│       └── knowledge_base/         # Document chunk store (SQLite + dense vectors)
├── skill_library/protocols/        # Protocol skills (SKILL.md + metadata.json)
├── vendor/
│   ├── firmware_library/           # Vendor firmware packages
│   └── toolchains/                 # Local toolchain installations
└── .luxar/                         # Runtime state, memory, local DBs
```

## 2. Workflow

### 2a. Standard Development Flow

1. `agent init --name <X> --mcu <Y>` — Creates project skeleton
2. User configures hardware in CubeMX, generates code into `Core/` and `Drivers/`
3. `agent assemble --project <X> --drivers <Y,Z>` — Retrieves/generates drivers, writes `App/`, reviews, commits
4. `agent build --project <X>` — Compiles with review gate before build
5. `agent flash --project <X> --probe stlink` — Flashes firmware to target
6. `agent monitor --project <X> --port COM3` — Observes serial output
7. `agent debug-loop --project <X> --port COM3` — Full auto build → flash → monitor → diagnose → fix cycle
8. `agent update-skill --project <X> --protocol <P>` — Extracts protocol knowledge after success

### 2b. Driver Development Flow

1. `agent parse-doc --doc <path> --query <Q>` — Ingests a datasheet into knowledge base
2. `agent search-driver --keyword <K>` — Checks if a driver already exists
3. `agent workflow driver --chip <C> --interface <I>` — Generate → review → fix → store pipeline
4. `agent assemble --project <X> --drivers <name>` — Installs driver into project

### 2c. Debug Loop Flow

1. Build attempt — On failure, classify error type (compile vs link vs toolchain)
2. Auto-repair — Apply compile-fix or link-context repair, then re-review
3. Flash attempt — On failure, retry with explicit probe config
4. Monitor attempt — On failure, retry with port release
5. If all attempts exhausted, report with recovery event log

## 3. Code Generation Rules

- All `App/` code uses HAL function-pointer injection, never global handles.
- Driver headers define a `xxx_hal_t` struct with function pointers.
- Driver source files must have matching header files.
- Every function must have a Doxygen comment.
- All pointer parameters must have NULL checks at entry.
- Return 0 for success, negative errno-style codes for failures.
- Never use `printf`/`malloc`/`free` in driver code.
- Never hardcode register addresses — use macros or enums.

## 4. Review Rules (Layer 2 Custom)

| Rule ID | Check | Severity |
|---------|-------|----------|
| EMB-001 | No global HAL handle references | Error |
| EMB-002 | USER CODE boundaries preserved in `main.c` | Error |
| EMB-003 | Doxygen comments required | Warning |
| EMB-004 | No `printf` in drivers | Error |
| EMB-005 | NULL pointer checks required | Error |
| EMB-006 | No hardcoded register addresses | Error |
| EMB-007 | No blocking HAL calls in ISRs | Error |
| EMB-008 | No `malloc`/`free` | Warning |
| EMB-009 | Cyclomatic complexity ≤ 15 | Warning |
| EMB-010 | `.c` must have matching `.h` | Error |

## 5. Testing

```bash
# Run all unit tests
python -m unittest discover -s tests -p "test_*.py" -v

# Run specific module tests
python -m unittest tests.unit.test_knowledge_base -v

# Run a single test
python -m unittest tests.unit.test_knowledge_base.KnowledgeBaseTests.test_store_and_search_document_chunks -v
```

## 6. Common Commands Reference

| Command | Purpose |
|---------|---------|
| `agent init --name <X> --mcu <Y>` | Create new project |
| `agent assemble --project <X> --drivers <Y,Z>` | Build project code |
| `agent build --project <X>` | Compile with review gate |
| `agent build --project <X> --skip-review` | Bypass review gate for iteration |
| `agent flash --project <X> --probe stlink` | Flash firmware |
| `agent monitor --project <X> --port COM3` | Capture serial output |
| `agent debug-loop --project <X> --port COM3` | Full auto cycle |
| `agent review --project <X> --file <F>` | Review single file |
| `agent review --project <X>` | Review entire project |
| `agent workflow driver --chip <C> --interface <I>` | Full driver pipeline |
| `agent workflow debug --project <X> --port COM3` | Full debug pipeline |
| `agent parse-doc --doc <path> --query <Q>` | Ingest document |
| `agent search-driver --keyword <K>` | Search driver library |
| `agent update-skill --protocol <P> --device <D>` | Write protocol skill |
| `agent config --show` | Show active configuration |
| `agent snapshot --project <X> --label <L>` | Manual snapshot |
| `agent diff --project <X>` | Show git diff since last human commit |

## 7. Pitfalls & Reminders

- **clang-tidy is optional** — review engine degrades gracefully when unavailable.
- **sentence-transformers is optional** — KB search falls back to sparse vectors.
- **LangGraph is optional** — workflows fall back to procedural pipeline.
- **Always check config first** — run `agent config --show` before starting work to confirm paths and provider.
- **Check events log on failure** — `logs/events.jsonl` contains the full audit trail.
- **Driver reuse uses exact-match first**, then heuristic scoring, then generation.
- **To parse PDF datasheets**, install `pypdf` or `pdfplumber` (not bundled).
- **Serial port must be released** — monitor and debug-loop auto-close; if interrupted, check manually.
- **Firmware-mode projects** do not require CubeMX; the agent generates all files.
- **If API key is missing**, set `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY` env var.

