# 新版 LUXAR 企业纵向切片实施与教学计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the learner-owned repository. Do not delegate the learner's core coding exercises to subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立仓库中完成一条从自然语言任务到结构化构建证据的可运行企业纵向切片，同时让学习者掌握 Agent 边界、LangGraph 运行时依赖注入、领域建模、Ports/Adapters、节点、路由和分层测试。

**Architecture:** 使用 Pydantic 2 定义领域对象，使用 `Protocol` 定义业务 Ports，使用 Fake Adapters 提供确定性外部能力，使用 LangGraph `StateGraph`、Runtime Context、条件边和 `Command` 编排纵向流程。第一阶段不调用真实 DeepSeek API 或 `idf.py`，但所有接口都按后续真实接入的形状设计。

**Tech Stack:** Python 3.12、LangGraph `>=1.2,<1.3`、Pydantic `>=2,<3`、pytest `>=8,<9`、标准库 `typing.Protocol`、`pathlib.Path`。

## Global Constraints

- 工作目录固定为 `C:\tmp\luxar-langgraph`，不得导入旧 `luxar` 包或学习仓库 `luxar_learning`。
- 测试固定使用 `C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider`。
- 教学目标优先于生成文件数量；每个新对象、API 和依赖方向必须在学习者编码前用中文直接讲清楚。
- 采用高效直讲：先给出技术答案、工程理由和当前小步正确代码，不让学习者先猜；每个关键检查点最多一个理解问题。
- 学习者亲自编写有学习价值的核心代码：领域模型、Port、Fake Adapter 的关键行为、Runtime Context、State、节点、路由和 Graph builder。
- Codex 负责 `pyproject.toml`、包标记、README、架构图、课程笔记、进度记录、测试样板、格式整理和提交说明。
- 测试用于验证完整小切片，不故意制造 import error、`NotImplementedError` 或预期失败作为教学路径。
- 每个任务结束时运行聚焦测试；每个阶段结束时运行完整测试并创建小提交。
- Domain 不导入 LangGraph、DeepSeek 客户端或 `subprocess`。
- Application 只依赖 Domain 和 Ports，不导入具体 Adapter。
- Adapter 将外部结果转换为 Domain 对象，不决定 Graph 下一步路线。
- API key、客户端、Adapter、Prompt 和日志对象不得进入 Graph State。
- 每个循环都有 State 计数器、最大次数、最终失败证据和显式终态。
- 第一阶段不加入真实 DeepSeek、真实 ESP-IDF、LangChain、数据库、CLI、Web UI、多 Agent、烧录或串口监控。

## 课程节奏

每个核心实现切片严格采用：

1. Codex 说明它在真实 Agent 系统中的作用。
2. Codex 展示它在依赖图或 State 流中的位置。
3. Codex 直接解释新名称、语法、职责和正确代码。
4. 学习者亲自输入当前核心代码。
5. Codex 读取实际文件并进行代码审查，不凭口头“完成”判断。
6. Codex 补齐机械测试或说明当前测试的工程价值。
7. 使用指定解释器运行测试，解释结果证明了什么。
8. Codex 自动维护文档和进度记录。

---

## File Map

| Path | Responsibility |
|---|---|
| `pyproject.toml` | 正式包结构、运行依赖和 pytest 配置 |
| `src/luxar/domain/requirements.py` | `FirmwareRequirement` 及完整性规则 |
| `src/luxar/domain/plans.py` | `PlanStep`、`ExecutionPlan` |
| `src/luxar/domain/evidence.py` | `BuildEvidence` 及真实证据不变量 |
| `src/luxar/domain/errors.py` | `WorkflowError` 和错误类别 |
| `src/luxar/ports/requirement_parser.py` | 需求解析业务 Port |
| `src/luxar/ports/planner.py` | 计划生成业务 Port |
| `src/luxar/ports/espidf.py` | ESP-IDF 构建业务 Port |
| `src/luxar/adapters/fake_requirement_parser.py` | 确定性需求解析测试实现 |
| `src/luxar/adapters/fake_planner.py` | 确定性计划生成测试实现 |
| `src/luxar/adapters/fake_espidf.py` | 可配置构建证据测试实现 |
| `src/luxar/application/state.py` | LangGraph 动态业务 State |
| `src/luxar/application/context.py` | 不持久化的运行依赖容器 |
| `src/luxar/application/nodes.py` | 调用 Ports 并返回 State 更新的节点 |
| `src/luxar/application/routing.py` | 纯路由和有限重试决策 |
| `src/luxar/application/graph.py` | Graph 拓扑与编译 |
| `tests/domain/` | 领域对象和不变量测试 |
| `tests/adapters/` | Port/Fake Adapter 契约测试 |
| `tests/application/` | 节点、路由和 Graph 测试 |
| `tests/integration/` | Fake 纵向链路端到端测试 |
| `docs/learning/` | Codex 维护的架构解释、复盘和进度 |

---

### Task 1: 建立正式 Python 包基线

**Files:**

- Codex creates: `pyproject.toml`
- Codex creates: `.gitignore`
- Codex creates: `README.md`
- Codex creates: package marker files under `src/luxar/`
- Codex creates: test directories and `docs/learning/PROGRESS.md`

**Interfaces:**

- Produces importable package `luxar` from a `src/` layout.
- Declares LangGraph, Pydantic, and pytest boundaries.

- [ ] **Step 1: Codex explains the enterprise package baseline**

Explain `pyproject.toml`, build backend, `src/` layout, runtime versus development dependencies, and why application code is not placed at repository root.

- [ ] **Step 2: Codex creates the scaffolding**

Use these dependency boundaries:

```toml
[project]
requires-python = ">=3.12,<3.13"
dependencies = [
    "langgraph>=1.2,<1.3",
    "pydantic>=2,<3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8,<9",
]
```

- [ ] **Step 3: Verify environment and package import**

Run:

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -c "import langgraph, pydantic; import luxar; print('luxar import: ok'); print('pydantic:', pydantic.__version__)"
```

Expected: package import succeeds with Pydantic 2 and LangGraph 1.2.x available.

- [ ] **Step 4: Commit the project baseline**

```powershell
git add .
git commit -m "build: establish enterprise Python project baseline"
```

---

### Task 2: 建模结构化固件需求

**Files:**

- Learner creates: `src/luxar/domain/requirements.py`
- Codex creates: `tests/domain/test_requirements.py`
- Codex updates: `docs/learning/01-domain-models.md`

**Interfaces:**

- Produces `FirmwareRequirement(BaseModel)`.
- Fields: `platform`, `target`, `feature`, `gpio`, `missing_fields`.
- Produces computed completeness rule through `is_complete`.

- [ ] **Step 1: Teach Domain model versus Graph State**

Explain why Pydantic validates a business object while `TypedDict` describes the Graph's changing container. Explain `BaseModel`, `Field(default_factory=list)`, `Literal`, optional values, immutability choice, and model-level business validation.

- [ ] **Step 2: Present the current core model**

The learner implements this production-shaped slice:

```python
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FirmwareRequirement(BaseModel):
    platform: Literal["espidf"] = "espidf"
    target: str
    feature: str
    gpio: int | None = None
    missing_fields: list[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields
```

- [ ] **Step 3: Codex adds domain tests**

Tests verify a complete requirement, an incomplete requirement, independent list defaults, and rejection of an unsupported platform.

- [ ] **Step 4: Run focused verification**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest tests/domain/test_requirements.py -v -p no:cacheprovider
```

- [ ] **Step 5: Commit the first domain object**

```powershell
git add src/luxar/domain/requirements.py tests/domain/test_requirements.py docs/learning/01-domain-models.md
git commit -m "feat: model structured firmware requirements"
```

---

### Task 3: 建模计划、证据和错误

**Files:**

- Learner creates: `src/luxar/domain/plans.py`
- Learner creates: `src/luxar/domain/evidence.py`
- Learner creates: `src/luxar/domain/errors.py`
- Codex creates: matching tests under `tests/domain/`
- Codex updates: `docs/learning/01-domain-models.md`

**Interfaces:**

- Produces `PlanStep`, `ExecutionPlan`, `BuildEvidence`, `WorkflowError`.

- [ ] **Step 1: Teach facts, intentions, and errors**

Explain that requirement is user intent, plan is proposed action, evidence is tool fact, and error is normalized failure. Emphasize that DeepSeek may propose a plan but cannot construct successful build evidence.

- [ ] **Step 2: Learner implements plan models**

```python
from typing import Literal

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    kind: Literal["create_project", "build_project"]
    description: str


class ExecutionPlan(BaseModel):
    steps: list[PlanStep] = Field(min_length=1)
```

- [ ] **Step 3: Learner implements evidence and error models**

`BuildEvidence` contains `success`, `command`, `return_code`, output summaries, and optional error category. `WorkflowError` contains stage, category, message, retryability, and user suggestion. Codex teaches and supplies the exact current-step implementation before typing.

- [ ] **Step 4: Codex adds invariant tests**

Tests verify non-empty plans, command preservation, success/failure evidence, and normalized retryability.

- [ ] **Step 5: Run all domain tests and commit**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest tests/domain -v -p no:cacheprovider
git add .
git commit -m "feat: distinguish plans evidence and workflow errors"
```

---

### Task 4: 用 Ports 表达外部能力

**Files:**

- Learner creates: three modules under `src/luxar/ports/`
- Codex creates: `tests/adapters/test_fake_contracts.py`
- Codex updates: `docs/learning/02-ports-and-adapters.md`

**Interfaces:**

```python
class RequirementParser(Protocol):
    def parse(self, task_text: str) -> FirmwareRequirement: ...

class Planner(Protocol):
    def create_plan(self, requirement: FirmwareRequirement) -> ExecutionPlan: ...

class EspIdfPort(Protocol):
    def build(self, project_path: Path) -> BuildEvidence: ...
```

- [ ] **Step 1: Teach dependency inversion and structural typing**

Explain `Protocol`, why Application owns the interface it needs, and why `isinstance` inheritance is not required for structural conformance.

- [ ] **Step 2: Learner implements one Port at a time**

Codex presents the exact import and signature for each Port, then reviews the actual file before continuing.

- [ ] **Step 3: Explain what Ports must not contain**

No prompt, API key, `subprocess`, retry routing, LangGraph State, or provider-specific response types.

- [ ] **Step 4: Verify static imports and commit**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
git add .
git commit -m "feat: define external capability ports"
```

---

### Task 5: 实现可控 Fake Adapters

**Files:**

- Learner creates: three Fake Adapter modules.
- Codex completes: `tests/adapters/test_fake_contracts.py`

**Interfaces:**

- `FakeRequirementParser` returns configured `FirmwareRequirement`.
- `FakePlanner` returns configured `ExecutionPlan`.
- `FakeEspIdf` returns configured evidence values in sequence and records calls.

- [ ] **Step 1: Teach Fake versus mock versus production Adapter**

Explain that Fake is a small working implementation with deterministic behavior, not a fabricated business claim in production.

- [ ] **Step 2: Learner implements Parser and Planner Fakes**

Use constructor-injected results so tests control outcomes without global variables.

- [ ] **Step 3: Learner implements sequenced ESP-IDF Fake**

The Fake records `Path` calls and returns evidence from a finite configured sequence. It raises a clear test-configuration error if the sequence is exhausted; it does not silently invent evidence.

- [ ] **Step 4: Codex verifies Port conformance through behavior tests**

Run:

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest tests/adapters -v -p no:cacheprovider
```

- [ ] **Step 5: Commit Fake Adapters**

```powershell
git add .
git commit -m "test: provide deterministic external adapters"
```

---

### Task 6: 定义 LangGraph State 与 Runtime Context

**Files:**

- Learner creates: `src/luxar/application/state.py`
- Learner creates: `src/luxar/application/context.py`
- Codex creates: `tests/application/test_state_boundaries.py`
- Codex updates: `docs/learning/03-state-and-runtime.md`

**Interfaces:**

- `WorkflowState(TypedDict, total=False)` references Domain objects and counters.
- `RuntimeContext` is a frozen dataclass containing the three Ports and `project_path`.

- [ ] **Step 1: Teach three kinds of data**

Directly contrast dynamic State, immutable invocation Context, and later persistent checkpoint snapshots. Explain why Adapter instances and secrets must not enter State.

- [ ] **Step 2: Learner implements State**

State fields: `task_text`, `requirement`, `plan`, `build_evidence`, `error`, `attempts`, `max_attempts`, `status`, and `trace`.

- [ ] **Step 3: Learner implements Runtime Context**

Use `@dataclass(frozen=True)` with `requirement_parser`, `planner`, `espidf`, and `project_path`.

- [ ] **Step 4: Codex verifies import directions**

Search to prove Domain does not import LangGraph or Adapters, and Application does not import concrete Fake classes.

- [ ] **Step 5: Run tests and commit**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
git add .
git commit -m "feat: separate workflow state from runtime dependencies"
```

---

### Task 7: 实现依赖注入节点

**Files:**

- Learner creates: `src/luxar/application/nodes.py`
- Codex creates: `tests/application/test_nodes.py`

**Interfaces:**

- Nodes accept `state: WorkflowState` and `runtime: Runtime[RuntimeContext]` where dependencies are required.
- Produces nodes: `analyze_requirement`, `create_plan`, `build_project`, `request_clarification`, `completed`, `failed`.

- [ ] **Step 1: Teach LangGraph Runtime injection**

Explain how LangGraph injects `Runtime[RuntimeContext]`, why node functions remain testable, and why `runtime.context` is not checkpointed.

- [ ] **Step 2: Learner implements one node per verified responsibility**

Parser node writes `FirmwareRequirement`; planner node writes `ExecutionPlan`; build node writes `BuildEvidence` and increments attempts. Nodes return minimal updates and never instantiate concrete Adapters.

- [ ] **Step 3: Codex adds direct node tests**

Tests use Fake Adapters and verify calls, State updates, trace order, and non-mutation.

- [ ] **Step 4: Run focused tests and commit**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest tests/application/test_nodes.py -v -p no:cacheprovider
git add .
git commit -m "feat: drive workflow nodes through runtime ports"
```

---

### Task 8: 实现证据驱动路由与有限重试

**Files:**

- Learner creates: `src/luxar/application/routing.py`
- Codex creates: `tests/application/test_routing.py`

**Interfaces:**

- Requirement router returns planning or clarification.
- Build decision returns `Command` to completed, build retry, or failed.

- [ ] **Step 1: Teach evidence-driven routing**

Contrast primitive `build_ok` with `BuildEvidence`. Explain why routing reads verified evidence and why environment errors may be non-retryable even when attempts remain.

- [ ] **Step 2: Learner implements pure requirement route**

Route using `requirement.is_complete`.

- [ ] **Step 3: Learner implements bounded build Command**

Decision order: success; retryable failure with budget; terminal failure. Preserve the last `BuildEvidence` in State.

- [ ] **Step 4: Codex adds complete route-table tests**

Cover complete/incomplete requirements, successful evidence, retryable failure, non-retryable failure, and configured limits 1–3.

- [ ] **Step 5: Run tests and commit**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest tests/application/test_routing.py -v -p no:cacheprovider
git add .
git commit -m "feat: route from structured evidence with bounded recovery"
```

---

### Task 9: 编译企业纵向 Graph

**Files:**

- Learner creates: `src/luxar/application/graph.py`
- Codex creates: `tests/application/test_graph.py`
- Codex creates: `tests/integration/test_fake_vertical_slice.py`
- Codex updates: README and learning docs.

**Interfaces:**

- Produces `build_graph() -> CompiledStateGraph` with `context_schema=RuntimeContext`.

- [ ] **Step 1: Teach final topology and composition root boundary**

Explain exactly which transitions are ordinary edges, conditional edges, and `Command` destinations. Explain compile-time topology versus invocation-time Adapter selection.

- [ ] **Step 2: Learner implements Graph builder**

Register six nodes, requirement conditional route, build recovery route, and all explicit terminal edges. Do not instantiate Fake or production Adapters in `graph.py`.

- [ ] **Step 3: Codex adds Graph and vertical integration tests**

Verify clarification, successful build, retry then success, terminal failure, call counts, final evidence, trace, and streaming node order.

- [ ] **Step 4: Run full verification gate**

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
rg -n "from luxar_learning|import luxar_learning" src tests
rg -n "openai|deepseek|subprocess|idf.py|langchain" src/luxar/domain src/luxar/application src/luxar/ports
```

Expected: all tests pass; no learning-repo import; no provider/tool implementation leaking into core layers.

- [ ] **Step 5: Codex records the architecture checkpoint**

Document the executable topology, dependency direction, State/Context split, Port replacement proof, and next entry point for the DeepSeek Adapter slice.

- [ ] **Step 6: Commit the first enterprise vertical slice**

```powershell
git add .
git commit -m "feat: complete fake-backed enterprise agent workflow"
```

---

## Final Learning Gate

Before starting the real DeepSeek slice, the verified repository must demonstrate:

1. Domain objects remain usable without importing LangGraph.
2. The same Graph runs with any objects satisfying the three Ports.
3. Runtime Context holds dependencies while State holds business progress.
4. LLM-produced requirement and plan data are validated before entering State.
5. Only an ESP-IDF Adapter can produce build execution evidence.
6. Graph tests prove retries terminate and preserve the last failure evidence.
7. The learner has personally implemented the core boundaries and can navigate the complete request-to-evidence path in code.

After this gate, create a separate implementation plan for `DeepSeekRequirementParser` and `DeepSeekPlanner`, including mocked API tests, JSON/Pydantic error normalization, secrets configuration, and an optional real-API smoke test.
