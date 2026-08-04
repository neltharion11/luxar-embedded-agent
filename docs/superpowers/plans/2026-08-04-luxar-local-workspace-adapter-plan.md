# LUXAR LocalWorkspaceAdapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not delegate the learner's core coding exercises to subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个只读取和修改 ESP-IDF 项目内既有源码文件、具备路径隔离、容量限制与多文件失败回滚能力的 `LocalWorkspaceAdapter`。

**Architecture:** `repair_project` 节点继续只依赖 `WorkspacePort`，不接触具体文件系统。新的本地 Adapter 在 I/O 前后同时验证路径和容量，通过同目录临时文件完成替换；工作区异常由唯一的工作流运行边界转换成安全的失败 State，不改变现有七节点 LangGraph 拓扑。

**Tech Stack:** Python 3.12、`pathlib`、`os`、`tempfile`、Pydantic `>=2,<3`、LangGraph `>=1.2,<1.3`、pytest `>=8,<9`。

## Global Constraints

- Repository: `C:\tmp\luxar-langgraph`。
- Tests: `C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider`。
- 只允许修改已经存在的文件；不得创建或删除项目文件。
- 可读写后缀：`.c .h .cc .cpp .hpp .s .S .cmake .ld .csv`。
- 可读写精确文件名：`CMakeLists.txt`、`Kconfig`、`Kconfig.projbuild`、`sdkconfig.defaults`、`idf_component.yml`、`project_include.cmake`。
- 不遍历 `.git`、`.vscode`、`.idea`、`build`、`build_*`、`managed_components`、`__pycache__` 和名称以 `.` 开头的目录。
- `sdkconfig`、`managed_components` 和 `dependencies.lock` 不交给模型修改。
- 项目根目录、目标及根目录以下的路径组件不得是符号链接或 Windows Junction。
- 默认单文件上限 `256 * 1024` 字节；一次调用的总上限 `1024 * 1024` 字节；构造器只接受正整数。
- 文件内容必须是严格 UTF-8 文本且不得包含 NUL 字节。
- 多文件修改必须先全部验证、再全部暂存、最后提交；普通提交失败时回滚已经替换的文件。
- `WorkspaceError` 不得包含绝对路径、原始 OS 异常、临时文件名或文件内容。
- 依赖预检属于后续 `EspIdfCliAdapter`；默认 `allow_dependency_downloads=False`，本切片不运行 `idf.py` 或下载依赖。
- 现有七节点 Graph 的节点和边保持不变；默认测试不访问网络。
- 学习者编写错误合同、真实 Adapter 与 Runner 接入等核心代码；Codex 编写测试、Markdown、进度记录和提交说明。
- 每次学习者编码前，先讲清调用链、语法和该安全规则解决的问题。

---

## 文件结构与职责

```text
src/luxar/ports/workspace_errors.py
  工作区能力的稳定异常合同，不依赖 Windows、DeepSeek 或 LangGraph

src/luxar/adapters/local_workspace.py
  实现 WorkspacePort；独占真实文件系统扫描、读取、暂存、替换和回滚

src/luxar/domain/errors.py
  让最终 WorkflowError 接受 provider-independent 的 workspace 类别

src/luxar/application/runner.py
  在唯一运行边界把 WorkspaceError 转成失败 WorkflowState

tests/ports/test_workspace_errors.py
  验证稳定异常合同

tests/adapters/test_local_workspace.py
  在 pytest tmp_path 中验证真实 I/O、安全边界和回滚

tests/application/test_runner.py
  验证工作区失败进入 State 且保留 requirement/plan/build evidence

docs/learning/08-local-workspace-adapter.md
  由 Codex 生成的中文复习笔记

docs/learning/PROGRESS.md
  由 Codex 同步真实测试结果和下一检查点
```

---

### Task 1: 建立 WorkspaceError 合同和工作流词汇

**Files:**

- Learner creates: `src/luxar/ports/workspace_errors.py`
- Learner modifies: `src/luxar/domain/errors.py`
- Codex creates: `tests/ports/test_workspace_errors.py`
- Codex modifies: `tests/domain/test_errors.py`

**Interfaces:**

`WorkspaceErrorCategory` 的完整词汇为：

```python
WorkspaceErrorCategory = Literal[
    "invalid_project",
    "unsafe_path",
    "unsupported_file",
    "file_too_large",
    "context_too_large",
    "invalid_encoding",
    "io",
    "rollback_failed",
]
```

`WorkspaceError.__init__` 的签名是
`(*, category: WorkspaceErrorCategory, message: str, retryable: bool) -> None`；
实例公开 `category`、`message`、`retryable` 三个属性。

`WorkflowError.category` additionally accepts `"workspace"`.

- [x] **Step 1: Codex 写入失败测试**

`tests/ports/test_workspace_errors.py` 用参数化测试构造全部八个类别，并验证 `str(error)`、`category`、`message` 和 `retryable`。`tests/domain/test_errors.py` 增加：

```python
def test_workflow_error_accepts_workspace_failure() -> None:
    error = WorkflowError(
        stage="repair",
        category="workspace",
        message="项目工作区操作失败",
        retryable=False,
    )

    assert error.category == "workspace"
```

- [x] **Step 2: 运行测试确认 RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/ports/test_workspace_errors.py tests/domain/test_errors.py
```

Expected: 新异常模块无法导入，且 `"workspace"` 尚未被 `WorkflowError` 接受。

- [x] **Step 3: 教学错误合同的职责**

讲清 `Literal` 规定允许出现的稳定词汇；`RuntimeError` 让对象可被 `raise/except`；三个实例属性让 Application 无需认识 `OSError`、`PermissionError` 等平台异常。这里的 Port 异常描述“能力失败”，Domain 的 `WorkflowError` 描述“整个 Agent 工作流的失败”。

- [x] **Step 4: 学习者实现 WorkspaceError**

实现内容为：

```python
from __future__ import annotations

from typing import Literal


WorkspaceErrorCategory = Literal[
    "invalid_project",
    "unsafe_path",
    "unsupported_file",
    "file_too_large",
    "context_too_large",
    "invalid_encoding",
    "io",
    "rollback_failed",
]


class WorkspaceError(RuntimeError):
    def __init__(
        self,
        *,
        category: WorkspaceErrorCategory,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.retryable = retryable
```

同时在 `WorkflowError.category` 的 `Literal` 中增加 `"workspace"`。

- [x] **Step 5: 运行聚焦测试确认 GREEN**

Expected: 新的 Port 与 Domain 测试全部通过。

- [x] **Step 6: 保存合同检查点**

Commit:

```text
feat: define workspace failure contract
```

---

### Task 2: 实现安全、确定性的项目源码读取

**Files:**

- Learner creates: `src/luxar/adapters/local_workspace.py`
- Codex creates: `tests/adapters/test_local_workspace.py`

**Interfaces:**

`LocalWorkspaceAdapter.__init__` 的签名是
`(max_file_bytes: int = 256 * 1024, max_total_bytes: int = 1024 * 1024) -> None`；
`read_project_files(project_path: Path) -> list[ProjectFile]` 实现现有
`WorkspacePort` 合同。

私有职责固定为：

- `_resolve_project_root(project_path: Path) -> Path`
- `_is_excluded_directory_name(name: str) -> bool`
- `_is_allowed_file_name(name: str) -> bool`
- `_is_link_or_junction(path: Path) -> bool`
- `_assert_no_link_components(root: Path, target: Path) -> None`

- [x] **Step 1: Codex 写构造器与正常读取的失败测试**

覆盖：限制必须是正整数；允许的精确文件名和后缀被读取；返回路径使用 `/`；输出按相对路径排序；中文 UTF-8 内容保持不变。例如：

```python
def test_read_project_files_returns_allowed_files_in_path_order(
    tmp_path: Path,
) -> None:
    (tmp_path / "main").mkdir()
    (tmp_path / "main" / "main.c").write_text(
        "// 中文\nvoid app_main(void) {}\n",
        encoding="utf-8",
    )
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n",
        encoding="utf-8",
    )

    files = LocalWorkspaceAdapter().read_project_files(tmp_path)

    assert [file.path for file in files] == [
        "CMakeLists.txt",
        "main/main.c",
    ]
```

- [x] **Step 2: Codex 写过滤与文本安全的失败测试**

覆盖：排除目录不遍历；`.txt`、`sdkconfig`、`dependencies.lock` 被忽略；允许文件中的 NUL 字节产生 `invalid_encoding`；非法 UTF-8 产生 `invalid_encoding`；单文件实际字节数与总字节数分别触发 `file_too_large` 和 `context_too_large`。

- [x] **Step 3: Codex 写根目录与链接安全的失败测试**

覆盖：不存在路径、普通文件充当根目录、根目录本身为 symlink/junction、允许文件为 symlink、根目录内中间目录为 symlink/junction。测试仅在当前系统不能创建相应链接时跳过。

- [x] **Step 4: 运行聚焦测试确认 RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/adapters/test_local_workspace.py -k "init or read"
```

Expected: `LocalWorkspaceAdapter` 尚不存在，测试收集失败。

- [x] **Step 5: 教学读取调用链和关键语法**

讲清：

```text
repair_project
  → workspace.read_project_files(project_path)
  → 校验并解析项目根目录
  → 确定性扫描且剪枝排除目录
  → 只选择 allowlist 文件
  → 检查链接、真实路径、stat 大小
  → read_bytes 后按实际长度复查
  → 拒绝 NUL / 严格 UTF-8 解码
  → 构造 ProjectFile(path, content)
```

解释 `Path.resolve(strict=True)`、`relative_to`、`suffix.lower()`、字节与字符的差别、`try/except UnicodeDecodeError`，以及为何不能使用字符串 `startswith` 判断路径是否越界。

- [x] **Step 6: 学习者实现构造器和纯判断函数**

常量与规则固定为：

```python
_ALLOWED_SUFFIXES = frozenset(
    {".c", ".h", ".cc", ".cpp", ".hpp", ".s", ".cmake", ".ld", ".csv"}
)
_ALLOWED_EXACT_NAMES = frozenset(
    {
        "CMakeLists.txt",
        "Kconfig",
        "Kconfig.projbuild",
        "sdkconfig.defaults",
        "idf_component.yml",
        "project_include.cmake",
    }
)
_EXCLUDED_EXACT_DIRECTORIES = frozenset(
    {".git", ".vscode", ".idea", "build", "managed_components", "__pycache__"}
)
```

目录名以 `.` 开头、等于固定排除名或以 `build_` 开头时排除。后缀通过 `Path(name).suffix.lower()` 比较，因此 `.S` 会规范化为 `.s`。

构造器使用：

```python
if (
    isinstance(max_file_bytes, bool)
    or not isinstance(max_file_bytes, int)
    or max_file_bytes <= 0
):
    raise ValueError("max_file_bytes must be a positive integer")
```

`max_total_bytes` 使用同样规则，防止 `True` 被当作整数 `1`。

- [x] **Step 7: 学习者实现根目录和链接校验**

`_is_link_or_junction` 同时调用 `path.is_symlink()` 与 Python 3.12 的 `path.is_junction()`。根目录必须先检查原路径是否为链接，再严格 `resolve`。目标安全验证沿着 `root` 到 `target` 的每个词法路径组件检查链接，随后严格解析并执行：

```python
try:
    resolved_target.relative_to(resolved_root)
except ValueError as error:
    raise WorkspaceError(
        category="unsafe_path",
        message="工作区路径不能离开项目目录",
        retryable=False,
    ) from error
```

所有外部异常都转换成固定文字，不拼接 `project_path` 或 `str(error)`。

- [x] **Step 8: 学习者实现 read_project_files**

扫描必须先过滤排除目录，再拒绝非排除范围内的链接目录；允许文件按 POSIX 相对路径排序。对每个文件先 `stat().st_size` 检查，再 `read_bytes()`，随后用 `len(data)` 复查；总量累计实际字节数。文本检查为：

```python
if b"\x00" in data:
    raise WorkspaceError(
        category="invalid_encoding",
        message="项目源码必须是 UTF-8 文本",
        retryable=False,
    )

try:
    content = data.decode("utf-8", errors="strict")
except UnicodeDecodeError as error:
    raise WorkspaceError(
        category="invalid_encoding",
        message="项目源码必须是 UTF-8 文本",
        retryable=False,
    ) from error
```

- [x] **Step 9: 运行读取测试确认 GREEN**

Expected: 正常读取、排除、容量、编码、symlink/junction 测试通过；只有系统确实无法创建链接的用例允许 skip。

- [x] **Step 10: 保存只读工作区检查点**

Commit:

```text
feat: read ESP-IDF workspace safely
```

---

### Task 3: 实现已有文件的完整内容替换

**Files:**

- Learner modifies: `src/luxar/adapters/local_workspace.py`
- Codex modifies: `tests/adapters/test_local_workspace.py`

**Interfaces:**

`apply_repair(project_path: Path, repair: RepairPlan) -> list[str]` 实现现有
`WorkspacePort` 写入合同。

每个内部待提交项目保存：规范相对路径、解析后的目标 `Path`、原始 `bytes`、替换 `bytes` 和临时文件 `Path`。

- [x] **Step 1: Codex 写成功替换的失败测试**

覆盖单文件和多文件完整替换，断言：内容实际改变；未在 `RepairPlan` 中出现的文件保持不变；返回路径顺序等于 `repair.replacements` 顺序；没有 `.luxar-*.tmp` 残留。

- [x] **Step 2: Codex 写“只改已有允许文件”的失败测试**

覆盖：目标不存在、目标是目录、目标后缀不受支持、绝对路径、`..` 越界路径、文件 symlink、父目录 symlink/junction。绝对路径和 `..` 通常先被 `FileReplacement` 的 Pydantic 验证拒绝；Adapter 测试继续证明磁盘层无法被链接绕过。

- [x] **Step 3: Codex 写替换容量和预验证失败测试**

覆盖：替换内容按 UTF-8 字节超过单文件限制；替换总量超过总限制；原文件超过回滚保存限制；第二个目标无效时第一个目标完全不变且没有临时文件。

- [x] **Step 4: 运行 apply 聚焦测试确认 RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/adapters/test_local_workspace.py -k "apply or replace"
```

Expected: `apply_repair` 尚未实现或无法完成替换。

- [x] **Step 5: 教学 validate-stage-commit 调用链**

讲清三个阶段的意义：验证阶段不产生副作用；暂存阶段只产生可清理的临时文件；提交阶段才改变真实文件。解释 `NamedTemporaryFile(delete=False, dir=target.parent)`、`flush()`、上下文管理器关闭文件，以及同目录 `os.replace` 为什么比直接 `write_text` 更适合完整文件替换。

- [x] **Step 6: 学习者实现“全部验证”**

对全部 replacement：复用 Task 2 的根目录、allowlist、包含关系和链接检查；要求目标存在且是普通文件；将 `replacement.content.encode("utf-8")` 作为新字节；读取原始字节用于回滚；分别限制每个原文件/新内容以及两组总字节数。任一验证失败时不得创建临时文件。

- [x] **Step 7: 学习者实现“全部暂存”**

每个临时文件必须位于其目标的父目录，名称前缀固定为 `.luxar-`、后缀为 `.tmp`。写入、刷新并关闭后才记录该临时路径。暂存阶段的 `OSError` 转换为：

```python
WorkspaceError(
    category="io",
    message="工作区文件写入失败",
    retryable=True,
)
```

`finally` 清理所有仍存在的临时文件；清理异常不能泄露原始 OS 文本。

- [x] **Step 8: 学习者实现“提交成功”**

每次 `os.replace(staged_path, target)` 前重新检查目标的包含关系与链接组件。成功后记录相对路径；全部完成后按 `repair.replacements` 原顺序返回路径。

- [x] **Step 9: 运行成功与限制测试确认 GREEN**

Expected: 正常替换与所有预验证测试通过，项目外文件、未列入计划的文件及无效计划下的文件均不变。

- [x] **Step 10: 保存安全写入检查点**

Commit:

```text
feat: apply existing-file workspace repairs
```

---

### Task 4: 为多文件提交增加失败回滚

**Files:**

- Learner modifies: `src/luxar/adapters/local_workspace.py`
- Codex modifies: `tests/adapters/test_local_workspace.py`

**Interfaces:**

普通提交失败：恢复所有已经替换的文件，然后抛出原来的安全 `WorkspaceError(category="io")`。

回滚失败：抛出 `WorkspaceError(category="rollback_failed", retryable=False)`。

- [ ] **Step 1: Codex 写后续替换失败的回滚测试**

通过 `monkeypatch` 包装 `luxar.adapters.local_workspace.os.replace`，让第二个正式替换抛出 `OSError`，但允许后续回滚替换执行。断言两个目标都恢复原始字节、异常类别为 `io`、消息不包含注入的敏感异常文字、没有临时文件残留。

- [ ] **Step 2: Codex 写回滚自身失败的测试**

让第二个正式替换和随后的回滚替换都失败。断言最终类别为 `rollback_failed`、`retryable is False`，消息不包含绝对路径或原始 `OSError` 文本。

- [ ] **Step 3: 运行回滚测试确认 RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/adapters/test_local_workspace.py -k "rollback"
```

Expected: 当前提交失败不会恢复已经替换的第一个文件。

- [ ] **Step 4: 教学异常期间的控制流**

解释已提交目标列表为何与全部目标列表不同、为什么按逆序回滚、
`raise 新异常 from 原异常` 建立的异常链不会自动进入 State，以及
`finally` 无论成功或失败都会执行。明确该方案处理普通 Python/I/O
失败，不宣称断电或进程崩溃下的跨文件事务原子性。

- [ ] **Step 5: 学习者实现回滚**

提交失败时，对 `committed` 逆序执行：在原父目录新建回滚临时文件、写入保存的原始字节、关闭后 `os.replace` 回目标。若任何回滚步骤失败，记住回滚失败并继续清理剩余临时文件，最终抛出固定的 `rollback_failed`。若回滚全部成功，抛出固定 `io` 错误。

- [ ] **Step 6: 运行整个 LocalWorkspaceAdapter 测试**

Expected: 读取、成功写入、拒绝规则、回滚和清理全部通过。

- [ ] **Step 7: 保存回滚检查点**

Commit:

```text
feat: rollback failed workspace repairs
```

---

### Task 5: 在统一 Runner 边界处理 WorkspaceError

**Files:**

- Learner modifies: `src/luxar/application/runner.py`
- Codex modifies: `tests/application/test_runner.py`

**Interfaces:**

`workspace_error_to_workflow_error(error: WorkspaceError) -> WorkflowError`
是一个不执行 I/O 的纯转换函数。

Runner 继续只有一个工作流异常捕获位置：

```python
except (CapabilityError, WorkspaceError) as error:
```

- [ ] **Step 1: Codex 写纯映射失败测试**

八个 Workspace 类别都映射到：

```python
WorkflowError(
    stage="repair",
    category="workspace",
    message=WORKSPACE_ERROR_MESSAGES[error.category],
    retryable=error.retryable,
    user_suggestion=WORKSPACE_ERROR_SUGGESTIONS[error.category],
)
```

测试向 `WorkspaceError.message` 注入敏感标记，并断言最终 message/suggestion 不包含该标记。

- [ ] **Step 2: Codex 写 Runner 集成失败测试**

提供一个测试本地 `RaisingWorkspace`，在 `read_project_files` 抛出配置好的 `WorkspaceError`。先让 Fake 构建返回含诊断的失败 `BuildEvidence`，再断言结果保留 requirement、plan、evidence、diagnostics 和 attempts，状态为 `failed`，error 为 `stage="repair"`、`category="workspace"`，trace 以 `failed` 结束。

- [ ] **Step 3: 运行 Runner 测试确认 RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/application/test_runner.py
```

Expected: `WorkspaceError` 仍会逃出 Runner，或映射函数尚不存在。

- [ ] **Step 4: 教学联合捕获与类型收窄**

讲清 `(CapabilityError, WorkspaceError)` 是 `except` 接受的异常类型元组，不是创建两个异常；`isinstance(error, CapabilityError)` 让 Python 和类型检查器知道应调用哪个转换函数。两个 Port 异常仍共享同一个 Application 边界。

- [ ] **Step 5: 学习者实现安全映射**

为八个类别分别定义固定中文消息和建议；不得使用 `error.message`。`workspace_error_to_workflow_error` 固定 `stage="repair"` 和 `category="workspace"`，只保留 `error.retryable`。

- [ ] **Step 6: 学习者扩展唯一捕获边界**

控制流为：

```python
except (CapabilityError, WorkspaceError) as error:
    if isinstance(error, CapabilityError):
        workflow_error = capability_error_to_workflow_error(
            error,
            latest_state,
        )
    else:
        workflow_error = workspace_error_to_workflow_error(error)

    failure_update = failed(latest_state)
    return cast(
        WorkflowState,
        {
            **latest_state,
            "error": workflow_error,
            **failure_update,
        },
    )
```

- [ ] **Step 7: 运行 Runner、Graph 和完整测试**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/application/test_runner.py tests/application/test_graph.py
```

然后运行完整要求命令。Expected: 原 CapabilityError 行为、七节点拓扑与 Fake 纵向链路均保持不变；真实 DeepSeek smoke 默认仍 skip。

- [ ] **Step 8: 保存应用接入检查点**

Commit:

```text
feat: handle workspace failures at workflow boundary
```

---

### Task 6: 安全审计、学习笔记和进度同步

**Files:**

- Codex creates: `docs/learning/08-local-workspace-adapter.md`
- Codex modifies: `docs/learning/00-LUXAR-Agent-复习总览.md`
- Codex modifies: `docs/learning/PROGRESS.md`
- Codex modifies: `README.md`
- Codex modifies: this plan

**Interfaces:**

文档记录真实完成状态和测试数字，不改变 Python 运行接口。

- [ ] **Step 1: Codex 生成中文复习笔记**

笔记必须解释：Workspace/工作区、Adapter/适配器、allowlist/允许列表、containment/路径包含、symlink/符号链接、junction/目录联接、staging/暂存、commit/提交替换、rollback/回滚、atomicity/原子性、byte budget/字节预算、sanitized error/脱敏错误。包含从 `repair_project` 到真实文件替换再回到 State 的纵向链路。

- [ ] **Step 2: Codex 更新入口文档和进度**

README 标明真实 Workspace Adapter 的使用边界；复习总览链接第 08 章；PROGRESS 记录每个检查点、最终测试数量，以及下一切片是具备依赖预检与默认禁止下载策略的 `EspIdfCliAdapter`。

- [ ] **Step 3: Codex 运行安全搜索**

检查：

```text
rg -n "startswith\(|absolute path|managed_components|dependencies\.lock|sdkconfig" src tests
rg -n "except .*WorkspaceError|except .*CapabilityError" src/luxar/application
```

人工确认没有字符串前缀路径判断、没有模型写入依赖目录、没有第二个业务异常边界、没有绝对路径进入稳定错误消息。

- [ ] **Step 4: Codex 运行最终验证**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
```

随后运行 `git diff --check` 并检查 `git status --short`。必须读取退出码和实际 pass/skip 数字后才能声明完成；`.vscode/` 保持未追踪且不提交。

- [ ] **Step 5: Codex 同步计划复选框并提交文档**

Commit:

```text
docs: complete local workspace adapter lesson
```

## Final Gate

1. 只有项目根目录内已经存在且在 allowlist 中的 ESP-IDF 文件能够被读取或替换。
2. Domain 路径验证和真实文件系统 containment 构成两层边界。
3. symlink 与 Junction 无法把读取或写入重定向到项目外。
4. 读取、替换和回滚保存均遵守可配置的字节预算。
5. 后续目标提交失败时，先前替换会被恢复；回滚失败具有独立稳定类别。
6. 临时文件在成功和可处理失败后都被清理。
7. WorkspaceError 在唯一 Runner 边界成为脱敏失败 State，且保留最近的 BuildEvidence。
8. 七节点 LangGraph 拓扑、Fake 测试和 DeepSeek Adapter 行为保持不变。
9. 本切片不运行 ESP-IDF、不下载依赖，也不修改工具管理的依赖产物。
