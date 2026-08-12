# LUXAR EspIdfCliAdapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not delegate the learner's core coding exercises to subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个默认禁止依赖下载、能够安全调用真实 `idf.py reconfigure` 与 `idf.py build`、并把有限且脱敏的终端输出转换成 `BuildEvidence` 的 `EspIdfCliAdapter`。

**Architecture:** 现有 `build_project` LangGraph 节点和 `EspIdfPort` 签名保持不变，正式运行时由 `RuntimeContext.espidf` 注入新的 CLI Adapter。Adapter 独占依赖清单预检、`subprocess`、输出分类和诊断解析；命令开始前的能力失败通过 `EspIdfError` 进入唯一 Runner 边界，命令开始后的结果通过 `BuildEvidence` 驱动现有路由。

**Tech Stack:** Python 3.12、`pathlib`、`subprocess`、`shutil`、`os`、`re`、PyYAML `>=6,<7`、Pydantic `>=2,<3`、LangGraph `>=1.2,<1.3`、pytest `>=8,<9`。

## Global Constraints

- Repository: `C:\tmp\luxar-langgraph`。
- Tests: `C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider`。
- 当前分支保持 `codex/luxar-langgraph-enterprise`；用户的 `.vscode/` 不修改、不暂存、不提交。
- `EspIdfPort.build(project_path: Path) -> BuildEvidence` 的公开签名保持不变。
- 现有七节点 Graph 的节点和边保持不变；`subprocess` 不得进入 Domain、Ports、Application 或 LangGraph 节点。
- `allow_dependency_downloads=False` 是默认值，并且只能由应用装配配置，不能来自 task text、Prompt、模型响应或 `WorkflowState`。
- 默认模式发现任一项目自有 `idf_component.yml` 中存在非空 `dependencies` 映射时，必须在启动任何 `idf.py` 命令前失败。
- 默认模式允许继续时，子进程环境必须包含 `IDF_COMPONENT_MANAGER=0`；显式授权模式不得强制写入该禁用值。
- 不扫描 `.git`、`.vscode`、`.idea`、`build`、`build_*`、`managed_components`、`__pycache__` 和名称以 `.` 开头的目录。
- 项目根目录和清单扫描路径不得经过符号链接或 Windows Junction。
- 清单使用 `yaml.safe_load`；禁止手写 YAML 解析器，禁止使用不安全的 `yaml.load`。
- `managed_components` 和 `dependencies.lock` 只由 ESP-IDF 工具管理，永远不交给模型或 Workspace Adapter 修改。
- 命令必须使用参数列表、`shell=False`、经过验证的 `cwd`、复制的环境、阶段超时和捕获输出。
- `BuildEvidence.command` 只保存 `['idf.py', 'reconfigure']` 或 `['idf.py', 'build']`，不得保存本机 Python、`idf.py` 或 ESP-IDF 安装目录的绝对路径。
- 命令前失败使用 `EspIdfError`；已经启动但超时使用 `BuildEvidence(return_code=-1, error_category='timeout')`。
- `dependency`、`environment`、`unknown` 进入 `failed`；只有 `source`、`linker` 可进入 `repair_project`；`timeout` 仅在预算内直接重试。
- 日志进入 State 前必须移除 ANSI 控制符、隐藏项目外绝对路径、限制长度；完整日志持久化不属于本切片。
- 默认测试不得访问网络，也不得要求本机存在 ESP-IDF；真实构建测试必须显式 opt-in。
- 学习者只编写有教学价值的错误合同、预检规则、命令编排、解析和应用接入核心代码；Codex 编写全部测试、fixtures、Markdown、进度记录和机械性脚手架。
- 每次学习者编码前，Codex 必须先用中文讲清调用链、涉及的 Python 语法、对象职责和安全规则。

---

## 文件结构与职责

```text
src/luxar/ports/espidf_errors.py
  ESP-IDF 能力在命令开始前失败时使用的稳定异常合同

src/luxar/adapters/espidf_cli.py
  实现 EspIdfPort；负责项目/清单预检、subprocess、日志脱敏、分类和诊断解析

src/luxar/domain/evidence.py
  BuildEvidence 增加 dependency 类别

src/luxar/domain/errors.py
  WorkflowError 增加 dependency 类别

src/luxar/application/routing.py
  明确 dependency 构建证据直接进入 failed

src/luxar/application/runner.py
  在唯一边界把 EspIdfError 转成安全 WorkflowError(stage='build')

src/luxar/bootstrap.py
  保留 Fake/自定义 Port 注入，同时能装配正式 EspIdfCliAdapter 与 LocalWorkspaceAdapter

pyproject.toml
  增加 PyYAML>=6,<7 运行时依赖

tests/ports/test_espidf_errors.py
  验证命令前稳定异常合同

tests/adapters/test_espidf_cli.py
  使用 tmp_path 和 monkeypatch 验证预检、命令、输出、超时和安全边界

tests/domain/test_evidence.py
tests/domain/test_errors.py
tests/application/test_routing.py
tests/application/test_runner.py
tests/test_bootstrap.py
  验证新词汇进入现有领域、路由、运行边界和组合根

tests/smoke/test_espidf_cli.py
  显式开关控制的真实 ESP-IDF 最小工程构建

docs/learning/09-espidf-cli-adapter.md
docs/learning/00-LUXAR-Agent-复习总览.md
docs/learning/PROGRESS.md
README.md
  Codex 维护的中文教学记录和使用边界
```

`espidf_cli.py` 暂时保持一个聚焦模块，因为预检、命令结果和解析都只服务一个 Adapter。当前不增加 `CommandRunner` Port；测试直接 monkeypatch 模块中的 `subprocess.run` 和 `shutil.which`。未来出现烧录、串口、Git 等多个 CLI Adapter 后，再根据真实重复提取进程执行抽象。

---

### Task 1: 建立 ESP-IDF 失败词汇和依赖类别

**Files:**

- Learner creates: `src/luxar/ports/espidf_errors.py`
- Learner modifies: `src/luxar/domain/evidence.py`
- Learner modifies: `src/luxar/domain/errors.py`
- Learner modifies: `src/luxar/application/routing.py`
- Codex creates: `tests/ports/test_espidf_errors.py`
- Codex modifies: `tests/domain/test_evidence.py`
- Codex modifies: `tests/domain/test_errors.py`
- Codex modifies: `tests/application/test_routing.py`

**Interfaces:**

```python
EspIdfErrorCategory = Literal[
    "invalid_project",
    "environment",
    "dependency",
    "process",
]

class EspIdfError(RuntimeError):
    def __init__(
        self,
        *,
        category: EspIdfErrorCategory,
        message: str,
        retryable: bool,
    ) -> None:
        ...
```

实例公开 `category`、`message`、`retryable`。`BuildEvidence.error_category` 和 `WorkflowError.category` 均增加 `"dependency"`。

- [ ] **Step 1: Codex 写错误合同和领域词汇的失败测试**

`tests/ports/test_espidf_errors.py` 使用参数化测试验证四个类别及 `str(error)`：

```python
@pytest.mark.parametrize(
    ("category", "retryable"),
    [
        ("invalid_project", False),
        ("environment", False),
        ("dependency", False),
        ("process", True),
    ],
)
def test_espidf_error_preserves_stable_failure_facts(
    category: EspIdfErrorCategory,
    retryable: bool,
) -> None:
    error = EspIdfError(
        category=category,
        message="stable message",
        retryable=retryable,
    )

    assert str(error) == "stable message"
    assert error.category == category
    assert error.message == "stable message"
    assert error.retryable is retryable
```

领域测试分别构造 `error_category="dependency"` 的失败 `BuildEvidence` 和 `category="dependency"`、`stage="build"` 的 `WorkflowError`。

- [ ] **Step 2: Codex 写 dependency 路由失败测试**

在 `tests/application/test_routing.py` 的参数表中增加：

```python
("dependency", 1, 3, "failed"),
```

它证明增加类别不会增加 Graph 节点或修复分支。

- [ ] **Step 3: 运行聚焦测试确认 RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/ports/test_espidf_errors.py tests/domain/test_evidence.py tests/domain/test_errors.py tests/application/test_routing.py
```

Expected: 新异常模块无法导入，并且两个 Pydantic `Literal` 尚不接受 `dependency`。

- [ ] **Step 4: 教学“异常”和“证据”的边界**

讲清：`EspIdfError` 表示 `idf.py` 没有完成一次可记录的命令；`BuildEvidence` 表示命令已经运行并产生事实。解释 `Literal`、`RuntimeError`、关键字专用参数 `*`、`super().__init__()`，以及为什么不能为“命令根本没启动”伪造 `return_code=1`。

- [ ] **Step 5: 学习者实现 EspIdfError 和两个 dependency 词汇**

`src/luxar/ports/espidf_errors.py` 的实现为：

```python
"""ESP-IDF Port 异常：描述命令开始前的稳定、可脱敏能力失败。"""

from __future__ import annotations

from typing import Literal


EspIdfErrorCategory = Literal[
    "invalid_project",
    "environment",
    "dependency",
    "process",
]


class EspIdfError(RuntimeError):
    def __init__(
        self,
        *,
        category: EspIdfErrorCategory,
        message: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.retryable = retryable
```

同时在 `BuildEvidence.error_category` 与 `WorkflowError.category` 的现有 `Literal` 中增加 `"dependency"`。路由实现无需增加新的 `if`：现有默认 `return "failed"` 已满足策略，但学习者要能解释这一事实。

- [ ] **Step 6: 运行聚焦测试确认 GREEN**

Expected: 新合同、领域模型和路由测试全部通过。

- [ ] **Step 7: 保存失败词汇检查点**

Commit:

```text
feat: define ESP-IDF failure contract
```

---

### Task 2: 实现项目、命令和依赖清单预检

**Files:**

- Codex modifies: `pyproject.toml`
- Learner creates: `src/luxar/adapters/espidf_cli.py`
- Codex creates: `tests/adapters/test_espidf_cli.py`

**Interfaces:**

```python
class EspIdfCliAdapter:
    def __init__(
        self,
        *,
        idf_command: Sequence[str] = ("idf.py",),
        allow_dependency_downloads: bool = False,
        reconfigure_timeout_seconds: int = 120,
        build_timeout_seconds: int = 600,
        max_summary_chars: int = 16_000,
        max_manifest_bytes: int = 256 * 1024,
        max_manifest_total_bytes: int = 1024 * 1024,
    ) -> None:
        ...
```

本任务实现构造器和 `_preflight(project_path: Path) -> tuple[Path, dict[str, str]]`，只验证这个独立的安全交付物。Task 3 再一次性加入完整可用的公开 `build()`，因此 Task 2 不留下会被误调用的占位方法或 `NotImplementedError`。

私有函数签名固定为：

```python
def _is_excluded_directory_name(name: str) -> bool: ...
def _is_link_or_junction(path: Path) -> bool: ...
def _resolve_project_root(project_path: Path) -> Path: ...
def _discover_manifests(root: Path) -> list[Path]: ...
def _manifest_has_dependencies(data: object) -> bool: ...
```

- [ ] **Step 1: Codex 增加 PyYAML 依赖并安装当前项目**

在 `pyproject.toml` 的运行时依赖中增加：

```toml
"PyYAML>=6,<7",
```

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pip install -e .
```

Expected: editable 安装成功，`python -c "import yaml; print(yaml.__version__)"` 显示 6.x。安装只更新 Conda 环境，不访问 ESP-IDF 组件注册表。

- [ ] **Step 2: Codex 写构造器失败测试**

覆盖：空命令、含空字符串的命令、布尔值或非正整数限制、非布尔下载授权。有效 `idf_command` 被复制成 tuple，调用方后来修改原 list 不影响 Adapter。

```python
def test_constructor_copies_idf_command() -> None:
    command = ["python", "idf.py"]
    adapter = EspIdfCliAdapter(idf_command=command)

    command.append("unsafe")

    assert adapter.idf_command == ("python", "idf.py")
```

- [ ] **Step 3: Codex 写项目和 launcher 预检失败测试**

覆盖：根目录不存在、普通文件充当根目录、根目录为 symlink/junction、缺少根 `CMakeLists.txt`、`CMakeLists.txt` 不是普通文件、默认 `idf.py` 无法通过 `shutil.which` 找到、显式绝对 launcher 不存在、显式绝对脚本不存在。异常必须是稳定类别且不包含测试绝对路径或注入的 OS 文本。

- [ ] **Step 4: Codex 写严格依赖授权失败测试**

使用 `tmp_path` 创建项目，monkeypatch `shutil.which` 返回测试 launcher。覆盖：

- 没有 manifest 时默认允许预检；
- 空 manifest 和没有 `dependencies` 的 mapping 默认允许；
- 非空 `dependencies` 在默认模式抛出 `EspIdfError(category="dependency")`；
- `allow_dependency_downloads=True` 时允许相同 manifest；
- 嵌套 component 下的 manifest 也会被发现；
- 排除目录中的 manifest 不会读取；
- 清单 symlink/junction 或中间链接目录被拒绝；
- manifest 超过单文件或总字节预算时产生 `invalid_project`；
- 非 UTF-8、NUL、无效 YAML、非 mapping 顶层、非 mapping 的 `dependencies` 都产生 `invalid_project`；
- 错误中不出现 manifest 内容或绝对路径。

关键断言示例：

```python
with pytest.raises(EspIdfError) as captured:
    adapter._preflight(project)

assert captured.value.category == "dependency"
assert subprocess_calls == []
```

- [ ] **Step 5: 运行预检测试确认 RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/adapters/test_espidf_cli.py -k "constructor or preflight or manifest"
```

Expected: `EspIdfCliAdapter` 尚不存在，测试收集失败。

- [ ] **Step 6: 教学构造器与预检调用链**

讲清：`Sequence[str]` 接受 list/tuple，但内部 tuple 防止外部修改；`shutil.which` 只检查受信任应用配置的首个命令；`yaml.safe_load` 返回 Python 对象后仍需检查顶层和 `dependencies` 类型；`os.walk(..., followlinks=False)` 仍需主动拒绝链接/Junction；`os.environ.copy()` 防止修改整个 Python 进程的环境。

调用链为：

```text
build(project_path)
  → _resolve_project_root
  → 检查 CMakeLists.txt
  → 检查受信任 idf_command
  → _discover_manifests
  → 逐个按字节预算读取并 yaml.safe_load
  → 未授权且存在依赖：EspIdfError(dependency)
  → 复制环境并按策略设置 IDF_COMPONENT_MANAGER
```

- [ ] **Step 7: 学习者实现构造器与路径/命令验证**

构造器对每个整数限制使用与 Workspace Adapter 相同的“拒绝 bool、要求正整数”规则。`allow_dependency_downloads` 必须满足 `isinstance(value, bool)`。命令验证规则：

```python
launcher = Path(self.idf_command[0])
if launcher.is_absolute():
    if not launcher.is_file():
        raise EspIdfError(
            category="environment",
            message="ESP-IDF 命令不可用",
            retryable=False,
        )
elif shutil.which(self.idf_command[0]) is None:
    raise EspIdfError(
        category="environment",
        message="ESP-IDF 命令不可用",
        retryable=False,
    )
```

`idf_command[1:]` 中是绝对路径的 token 必须存在且是普通文件；相对 token 由受信任配置负责，不按项目内容解释。

- [ ] **Step 8: 学习者实现安全 manifest 扫描与解析**

排除规则与 LocalWorkspaceAdapter 保持一致。扫描只收集精确名 `idf_component.yml`，按项目相对 POSIX 路径排序。读取前后检查实际字节数，拒绝 NUL 并严格 UTF-8 解码。解析为：

```python
try:
    loaded = yaml.safe_load(text)
except yaml.YAMLError as error:
    raise EspIdfError(
        category="invalid_project",
        message="ESP-IDF 依赖清单无效",
        retryable=False,
    ) from error

if loaded is None:
    loaded = {}

if not isinstance(loaded, dict):
    raise EspIdfError(
        category="invalid_project",
        message="ESP-IDF 依赖清单无效",
        retryable=False,
    )

dependencies = loaded.get("dependencies")
if dependencies is None:
    return False
if not isinstance(dependencies, dict):
    raise EspIdfError(
        category="invalid_project",
        message="ESP-IDF 依赖清单无效",
        retryable=False,
    )
return bool(dependencies)
```

不得把 `str(error)`、路径或 YAML 内容拼入稳定消息。

- [ ] **Step 9: 学习者实现 _preflight 的授权环境**

返回解析后的根目录与复制环境：

```python
environment = os.environ.copy()
environment["IDF_COMPONENT_NO_COLORS"] = "1"
environment["IDF_COMPONENT_NO_HINTS"] = "1"

if not self.allow_dependency_downloads:
    environment["IDF_COMPONENT_MANAGER"] = "0"
```

显式授权模式应从复制环境删除已有的禁用值，确保应用的授权决定不会被父进程遗留的 `IDF_COMPONENT_MANAGER=0` 静默否定：

```python
else:
    environment.pop("IDF_COMPONENT_MANAGER", None)
```

- [ ] **Step 10: 运行预检测试确认 GREEN**

Expected: 构造器、根目录、launcher、YAML、容量、链接与授权测试全部通过；只有当前系统不能创建普通 symlink 的用例允许 skip。

- [ ] **Step 11: 保存依赖预检检查点**

Commit:

```text
feat: preflight ESP-IDF projects and dependencies
```

---

### Task 3: 安全执行 reconfigure 和 build

**Files:**

- Learner modifies: `src/luxar/adapters/espidf_cli.py`
- Codex modifies: `tests/adapters/test_espidf_cli.py`

**Interfaces:**

本任务新增公开 Port 实现和私有命令方法：

```python
def build(self, project_path: Path) -> BuildEvidence:
    ...

def _run_action(
    self,
    *,
    action: Literal["reconfigure", "build"],
    root: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> BuildEvidence:
    ...

def _logical_command(action: str) -> list[str]:
    return ["idf.py", action]
```

Task 3 暂时把所有普通非零返回码分类为 `unknown`；Task 4 再以纯解析函数替换该分类，并增加诊断。

- [ ] **Step 1: Codex 写两阶段成功和提前终止测试**

monkeypatch `subprocess.run` 返回预设 `CompletedProcess` 队列。验证：

- 成功时调用顺序严格是 `reconfigure`、`build`；
- `reconfigure` 非零时不调用 `build`；
- 成功返回最终 build evidence；
- reconfigure 失败返回 reconfigure evidence；
- `command` 只记录逻辑命令，不包含测试使用的绝对 launcher。

```python
assert calls[0][0] == [*trusted_prefix, "reconfigure"]
assert calls[1][0] == [*trusted_prefix, "build"]
assert evidence.command == ["idf.py", "build"]
```

- [ ] **Step 2: Codex 写 subprocess 安全参数测试**

每次调用必须断言：

```python
assert kwargs["cwd"] == project.resolve()
assert kwargs["shell"] is False
assert kwargs["capture_output"] is True
assert kwargs["text"] is True
assert kwargs["encoding"] == "utf-8"
assert kwargs["errors"] == "replace"
assert kwargs["check"] is False
```

同时验证 reconfigure/build 使用各自超时，环境是新字典，且没有修改 `os.environ`。

- [ ] **Step 3: Codex 写超时和启动失败测试**

`subprocess.TimeoutExpired` 分别在两个阶段产生：

```python
BuildEvidence(
    success=False,
    command=["idf.py", action],
    return_code=-1,
    stdout_summary="timeout output before deadline",
    stderr_summary="ESP-IDF command timed out",
    error_category="timeout",
)
```

超时对象携带的 stdout/stderr 可以是 `str` 或 `bytes`，均必须安全转换。`OSError` 启动失败产生 `EspIdfError(category="process")`，消息不含注入的敏感路径。reconfigure 超时或启动失败后不得运行 build。

- [ ] **Step 4: 运行命令测试确认 RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/adapters/test_espidf_cli.py -k "command or reconfigure or build or timeout or process"
```

Expected: `build` 尚未完成两阶段编排。

- [ ] **Step 5: 教学 subprocess.run 的参数和返回值**

解释：参数列表不会再由 PowerShell/CMD 解析；`cwd` 决定 ESP-IDF 项目；`CompletedProcess.returncode` 是真实退出码；stdout/stderr 是两个输出通道；`check=False` 让 Adapter 自己把失败变成领域证据；`TimeoutExpired` 是“已经启动但没有完成”；`OSError` 是“进程无法启动”。

- [ ] **Step 6: 学习者实现 _run_action**

核心调用固定为：

```python
try:
    result = subprocess.run(
        [*self.idf_command, action],
        cwd=root,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout_seconds,
        env=environment,
    )
except subprocess.TimeoutExpired as error:
    return BuildEvidence(
        success=False,
        command=_logical_command(action),
        return_code=-1,
        stdout_summary=_coerce_timeout_output(error.stdout),
        stderr_summary=_coerce_timeout_output(error.stderr),
        error_category="timeout",
    )
except OSError as error:
    raise EspIdfError(
        category="process",
        message="ESP-IDF 进程无法启动",
        retryable=True,
    ) from error
```

普通结果：返回码为 0 时 `success=True` 且类别为 `None`；非零时 `success=False` 且暂为 `unknown`。

- [ ] **Step 7: 学习者完成 build 两阶段编排**

```python
def build(self, project_path: Path) -> BuildEvidence:
    root, environment = self._preflight(project_path)

    reconfigure = self._run_action(
        action="reconfigure",
        root=root,
        environment=environment,
        timeout_seconds=self.reconfigure_timeout_seconds,
    )
    if not reconfigure.success:
        return reconfigure

    return self._run_action(
        action="build",
        root=root,
        environment=environment,
        timeout_seconds=self.build_timeout_seconds,
    )
```

这段只做编排，不分析自然语言，也不决定下一个 LangGraph 节点。

- [ ] **Step 8: 运行命令测试确认 GREEN**

Expected: 两阶段、参数安全、提前终止、超时和启动失败全部通过。

- [ ] **Step 9: 保存真实命令检查点**

Commit:

```text
feat: run ESP-IDF configure and build safely
```

---

### Task 4: 将终端输出转换为脱敏诊断证据

**Files:**

- Learner modifies: `src/luxar/adapters/espidf_cli.py`
- Codex modifies: `tests/adapters/test_espidf_cli.py`

**Interfaces:**

新增纯函数：

```python
def _strip_ansi(text: str) -> str: ...
def _sanitize_output(text: str, root: Path, max_chars: int) -> str: ...
def _classify_failure(action: str, stdout: str, stderr: str) -> BuildErrorCategory: ...
def _parse_diagnostics(text: str, root: Path) -> list[BuildDiagnostic]: ...
```

在 Adapter 模块内定义：

```python
BuildErrorCategory = Literal[
    "dependency",
    "environment",
    "source",
    "linker",
    "unknown",
]
```

`timeout` 继续由异常分支直接产生，不由文本分类器猜测。

- [ ] **Step 1: Codex 写分类优先级失败测试**

参数化覆盖：

```text
Failed to resolve component / registry / managed_components → dependency
CMake 或 Ninja/编译器/Python 模块不可用              → environment
undefined reference / multiple definition / ld returned   → linker
file:line:column: error                                   → source
无法识别的非零输出                                         → unknown
```

混合输出必须证明优先级：依赖文本同时含 `CMake Error` 仍是 dependency；链接文本同时含普通 `error` 仍是 linker。

- [ ] **Step 2: Codex 写 GCC/Clang 和 CMake 诊断失败测试**

覆盖：

```text
C:\project\main\main.c:42:17: error: expected ';'
main/component.cpp:8: warning: unused variable
CMake Error at main/CMakeLists.txt:12 (idf_component_register):
```

期望得到项目相对 POSIX 路径、从 1 开始的行列号、规范 severity 和非空消息。相同诊断重复出现时只保留一次并保持首次出现顺序。项目外绝对路径的 `BuildDiagnostic.file` 为 `None`。

- [ ] **Step 3: Codex 写日志清理和长度失败测试**

验证：ANSI 转义被删除；CRLF 统一成 LF；项目根绝对路径替换成项目相对表示；已识别的 Windows/POSIX 项目外绝对路径替换为 `<external-path>`；输出按 `max_summary_chars` 确定性截断；构造器的小长度值可用于测试。断言 State 摘要中不存在用户目录、ESP-IDF 安装路径和颜色控制码。

- [ ] **Step 4: 运行解析测试确认 RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/adapters/test_espidf_cli.py -k "classif or diagnostic or sanitize or ansi or summary"
```

Expected: 当前非零结果仍全部为 `unknown`，且没有结构化诊断。

- [ ] **Step 5: 教学“正则提取”和“LLM 语义修复”的分工**

讲清：正则不是让 Agent 理解代码，而是把编译器固定格式拆成字段；返回码决定成功/失败；关键词和阶段决定错误类别；LLM 只在后续看到结构化 source/linker 证据后推理如何修改源码。解释 raw string、命名捕获组、Windows 盘符中的冒号、`Path.relative_to`、去重集合与保持顺序。

- [ ] **Step 6: 学习者实现清理和分类**

ANSI 模式使用：

```python
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
```

分类器把 stdout/stderr 拼成小写文本，按严格顺序判断 `dependency → environment → linker → source → unknown`。模式集合以明确短语保存，不使用模糊的单个 `error` 作为 source 依据。

- [ ] **Step 7: 学习者实现诊断解析与路径脱敏**

GCC/Clang 正则必须从行尾的 `:line[:column]: severity: message` 反向约束，避免 Windows `C:` 盘符破坏文件组。对诊断路径只做词法规范化和项目包含判断，不跟随编译器报告的路径读取文件。CMake 消息取当前行及紧随其后的第一条非空说明作为 message，找不到时使用稳定的 `CMake configuration error`。

- [ ] **Step 8: 学习者把解析器接入 _run_action**

顺序固定：先对原始 stdout/stderr 分类和解析，再分别生成脱敏摘要。失败 evidence 使用分类结果；成功 evidence 可保留有限成功摘要但 diagnostics 默认为空。超时分支也必须调用同一脱敏函数，不能直接保存异常原文。

- [ ] **Step 9: 运行整个 Adapter 测试确认 GREEN**

Expected: 预检、命令、分类、诊断、脱敏和长度测试全部通过。

- [ ] **Step 10: 保存证据解析检查点**

Commit:

```text
feat: parse ESP-IDF build evidence
```

---

### Task 5: 接入唯一 Runner 边界和正式组合根

**Files:**

- Learner modifies: `src/luxar/application/runner.py`
- Learner modifies: `src/luxar/bootstrap.py`
- Codex modifies: `tests/application/test_runner.py`
- Codex modifies: `tests/test_bootstrap.py`
- Codex modifies: `tests/application/test_graph.py`

**Interfaces:**

```python
def espidf_error_to_workflow_error(
    error: EspIdfError,
) -> WorkflowError:
    ...
```

`run_workflow` 仍只有一个捕获位置：

```python
except (CapabilityError, WorkspaceError, EspIdfError) as error:
```

`build_deepseek_runtime_context` 的兼容签名调整为：

```python
def build_deepseek_runtime_context(
    *,
    project_path: Path,
    espidf: EspIdfPort | None = None,
    workspace: WorkspacePort | None = None,
    settings: DeepSeekSettings | None = None,
    client: JsonCompletionClient | None = None,
    allow_dependency_downloads: bool = False,
    idf_command: Sequence[str] = ("idf.py",),
) -> RuntimeContext:
    ...
```

显式传入 Fake/自定义对象时保持对象身份；未传时分别创建 `EspIdfCliAdapter` 和 `LocalWorkspaceAdapter`。

- [ ] **Step 1: Codex 写 EspIdfError 映射失败测试**

四个类别映射为：

```text
invalid_project → stage=build, category=environment
environment     → stage=build, category=environment
dependency      → stage=build, category=dependency
process         → stage=build, category=environment
```

最终 message/suggestion 必须来自 Application 固定字典，不能包含注入到 `EspIdfError.message` 的敏感标记。`retryable` 保留异常声明。

- [ ] **Step 2: Codex 写 Runner 纵向失败测试**

定义测试 `RaisingEspIdf` 实现 `build()` 并抛出配置错误。完整 Graph 先完成 requirement 和 plan，再在 build 失败。断言结果保留 requirement、plan 和最新 trace，状态为 `failed`，错误阶段是 build，且 `attempts` 不增加——因为节点没有收到任何完成的 BuildEvidence。

- [ ] **Step 3: Codex 写组合根默认与注入测试**

现有显式 Fake 注入测试保持通过；新增测试验证省略 `espidf/workspace` 时 Context 包含：

```python
assert isinstance(context.espidf, EspIdfCliAdapter)
assert isinstance(context.workspace, LocalWorkspaceAdapter)
assert context.espidf.allow_dependency_downloads is False
```

另一个测试传入 `allow_dependency_downloads=True` 和自定义 `idf_command`，验证它们只进入新建 Adapter；显式传入 `espidf` 时这些构造参数不替换传入对象。

- [ ] **Step 4: 运行 Runner 和 bootstrap 测试确认 RED**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider tests/application/test_runner.py tests/test_bootstrap.py tests/application/test_graph.py
```

Expected: `EspIdfError` 尚未被统一边界捕获，且组合根仍要求显式提供两个工具 Port。

- [ ] **Step 5: 教学三种错误如何共享一个边界**

讲清联合 `except` 的 tuple 是允许捕获的异常类型集合；`isinstance` 分支将模型、工作区、ESP-IDF 三种 Port 错误交给各自纯转换函数；`latest_state` 只包含已经成功完成的节点，所以命令前失败不会伪造 attempts 或 evidence。组合根负责选择真实 Adapter，节点仍只认识 Port。

- [ ] **Step 6: 学习者实现安全映射和联合捕获**

增加固定字典：

```python
ESPIDF_ERROR_MESSAGES = {
    "invalid_project": "ESP-IDF 项目结构无效",
    "environment": "ESP-IDF 构建环境不可用",
    "dependency": "项目依赖需要显式授权后才能解析",
    "process": "ESP-IDF 构建进程无法启动",
}

ESPIDF_ERROR_SUGGESTIONS = {
    "invalid_project": "请检查项目根目录和 CMakeLists.txt",
    "environment": "请在已激活的 ESP-IDF 环境中重试",
    "dependency": "请确认依赖来源后显式允许依赖下载",
    "process": "请检查 ESP-IDF 命令、权限和运行环境",
}
```

转换函数固定 `stage="build"`，类别按上表映射，禁止使用 `error.message`。

- [ ] **Step 7: 学习者实现正式 Adapter 默认装配**

在 `bootstrap.py` 导入正式两个 Adapter。创建默认对象的逻辑为：

```python
if espidf is None:
    espidf = EspIdfCliAdapter(
        idf_command=idf_command,
        allow_dependency_downloads=allow_dependency_downloads,
    )

if workspace is None:
    workspace = LocalWorkspaceAdapter()
```

DeepSeek Settings 和共享 Client 的现有装配顺序保持不变。`EspIdfCliAdapter` 构造时不访问文件系统或查找命令，真实检查只在 `build()` 中发生，因此普通 bootstrap 测试不需要安装 ESP-IDF。

- [ ] **Step 8: 运行应用与完整测试确认 GREEN**

先运行聚焦命令，再运行规定的完整测试。Expected: 新错误正确进入 failed State；现有 CapabilityError、WorkspaceError、Fake 纵向链路和七节点拓扑不变。

- [ ] **Step 9: 保存应用接入检查点**

Commit:

```text
feat: wire ESP-IDF adapter into workflow
```

---

### Task 6: 增加显式真实 smoke、完成安全审计和教学记录

**Files:**

- Codex creates: `tests/smoke/test_espidf_cli.py`
- Codex creates: `docs/learning/09-espidf-cli-adapter.md`
- Codex modifies: `docs/learning/00-LUXAR-Agent-复习总览.md`
- Codex modifies: `docs/learning/PROGRESS.md`
- Codex modifies: `README.md`
- Codex modifies: this plan

**Interfaces:**

真实 smoke 由两个条件共同控制：

```text
LUXAR_RUN_ESPIDF_SMOKE=1
idf.py 可通过当前已激活环境发现
```

它使用 pytest `tmp_path` 创建一个无托管依赖的最小 ESP-IDF 项目，只验证默认禁下载模式的 `reconfigure/build` 成功证据。

- [ ] **Step 1: Codex 写默认跳过的真实 smoke 测试**

测试创建：

```text
CMakeLists.txt
main/CMakeLists.txt
main/main.c
```

根文件使用 ESP-IDF 标准 `project.cmake`；`main/main.c` 只包含空的 `app_main`。没有 `idf_component.yml`。未设置开关或找不到 `idf.py` 时使用 `pytest.skip`；不得自动安装 ESP-IDF，不得下载组件，不得使用用户工程。

- [ ] **Step 2: Codex 运行默认完整测试**

Run:

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
```

Expected: 所有离线测试通过；真实 DeepSeek 与真实 ESP-IDF smoke 默认显示 skip。必须记录实际 collected/pass/skip 数字，不能预先填写结果。

- [ ] **Step 3: Codex 执行结构和安全搜索**

Run:

```text
rg -n "import subprocess|from subprocess" src/luxar
rg -n "shell=True|os\.system|Popen\(" src tests
rg -n "IDF_COMPONENT_MANAGER|allow_dependency_downloads|managed_components|dependencies\.lock" src tests
rg -n "except .*CapabilityError|except .*WorkspaceError|except .*EspIdfError" src/luxar/application
rg -n "yaml\.load\(" src tests
```

人工确认：`subprocess` 只在 `adapters/espidf_cli.py`；没有 shell 字符串执行；默认禁下载测试存在；模型不能修改工具依赖目录；Application 只有一个联合异常边界；只使用 `yaml.safe_load`。

- [ ] **Step 4: Codex 生成中文复习笔记**

第 09 章必须解释英文名词与中文对应：CLI、subprocess、process、launcher、working directory/cwd、stdout、stderr、return code、timeout、preflight、manifest、dependency resolution、Component Manager、sanitization、diagnostic、classification、smoke test。包含以下三条纵向链路：

```text
正常构建：node → Port → Adapter → reconfigure → build → evidence → completed
源码失败：build → source diagnostic → repair_project → rebuild
命令前失败：Adapter → EspIdfError → Runner → WorkflowError → failed
```

笔记还要解释：为什么它仍然是 Agent 而不是简单脚本；哪些判断由 LLM 做、哪些由确定性代码做；`subprocess.run` 各参数的语法；为什么默认禁止下载是权限边界而不是 Prompt。

- [ ] **Step 5: Codex 同步 README、总览和 PROGRESS**

只记录真实完成状态、实际测试数字、真实 smoke 的运行条件和下一技术切片。下一切片根据当前总体设计进入“真实 ESP-IDF 最小工程创建/计划审批与持久化”中的优先项，不在本任务预先实施。

- [ ] **Step 6: Codex 运行最终验证和 Git 检查**

再次运行规定完整测试，然后运行：

```text
git diff --check
git status --short
```

`.vscode/` 必须保持未跟踪且不进入暂存区。读取实际退出码后才能声称完成。

- [ ] **Step 7: Codex 同步计划复选框并提交文档**

Commit:

```text
docs: complete ESP-IDF CLI adapter lesson
```

## Final Gate

1. `EspIdfCliAdapter` 满足不变的 `EspIdfPort`，七节点 Graph 拓扑不改变。
2. 默认模式下，只要项目自有 manifest 声明非空依赖，就不会启动任何可能联网的 ESP-IDF 命令。
3. 显式授权只来自应用装配配置；State 和 LLM 都不能扩大权限。
4. `reconfigure` 成功后才执行 `build`；失败证据记录实际终止阶段。
5. 所有命令使用受信任参数列表、`shell=False`、验证后的 cwd、复制环境和阶段超时。
6. 命令前失败是稳定脱敏的 `EspIdfError`；命令完成或超时后是符合事实的 `BuildEvidence`。
7. dependency/environment/unknown 不进入源码修复；source/linker 才把诊断交给 RepairPlanner；timeout 受 attempts 上限约束。
8. 项目内诊断路径相对化，项目外绝对路径和 ANSI 控制符不进入 State，摘要长度有固定上限。
9. 默认测试不依赖网络或 ESP-IDF 安装，真实 smoke 只能显式启用且使用临时无依赖项目。
10. Domain、Ports、Application 和节点不导入 `subprocess`，`managed_components` 与 `dependencies.lock` 仍由工具独占。
