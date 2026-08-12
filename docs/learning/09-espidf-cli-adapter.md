# 09：EspIdfCliAdapter 与真实构建证据

这一章复习 LUXAR 如何安全调用真实 `idf.py`。它是模型世界和嵌入式工程
世界之间的桥：模型可以提出需求、计划和源码修复，但只有真实编译器返回的
`BuildEvidence` 才能证明工程是否构建成功。

## 一、它在整体架构中的位置

```text
LangGraph 的 build_project 节点
  → EspIdfPort.build(project_path)
  → EspIdfCliAdapter
  → 项目、命令、依赖清单预检
  → idf.py reconfigure
  → idf.py build
  → 日志分类、诊断提取、路径脱敏
  → BuildEvidence
  → LangGraph 根据证据路由
```

节点只认识 `EspIdfPort`，不知道 `subprocess`。正式运行时，`bootstrap.py`
把 `EspIdfCliAdapter` 注入 `RuntimeContext`；测试则可注入 `FakeEspIdf`。
因此真实工具更换不会迫使七节点 Graph 改写。

## 二、英文名词与中文含义

| 英文 | 中文 | 当前代码中的含义 |
|---|---|---|
| CLI | 命令行接口 | `idf.py` 提供的工程命令 |
| subprocess | 子进程 | Python 启动的外部 ESP-IDF 进程 |
| Process | 进程 | 正在运行的独立程序实例 |
| Launcher | 启动程序 | 命令列表中的第一个程序，如 `idf.py` |
| Working Directory / cwd | 工作目录 | 命令执行时认定的 ESP-IDF 项目根目录 |
| stdout | 标准输出 | 命令的普通输出通道 |
| stderr | 标准错误 | 命令的错误和诊断输出通道 |
| Return Code | 返回码 | `0` 表示成功，非零表示命令失败 |
| Timeout | 超时 | 命令已经启动，但未在规定时间内结束 |
| Preflight | 前置检查 | 启动命令前完成的项目、环境和权限检查 |
| Manifest | 清单 | `idf_component.yml` 依赖声明文件 |
| Dependency Resolution | 依赖解析 | 根据清单定位或下载组件 |
| Component Manager | 组件管理器 | ESP-IDF 管理依赖组件的工具 |
| Sanitization | 脱敏/清理 | 删除 ANSI、绝对路径并限制日志长度 |
| Diagnostic | 诊断 | 文件、行列、严重性和错误消息 |
| Classification | 分类 | 把失败归为依赖、环境、源码、链接等类别 |
| Smoke Test | 冒烟测试 | 用最小真实工程验证外部工具确实可用 |

## 三、三条纵向链路

### 正常构建

```text
build_project
→ EspIdfPort
→ EspIdfCliAdapter
→ reconfigure 成功
→ build 成功
→ BuildEvidence(success=True)
→ completed
```

### 源码失败后修复

```text
idf.py build 非零退出
→ 提取 source/linker diagnostic
→ BuildEvidence(success=False)
→ repair_project
→ DeepSeekRepairPlanner 生成受验证的完整文件替换
→ LocalWorkspaceAdapter 只修改已有允许文件
→ 再次 build_project
```

模型提出修复不代表成功。新一轮真实构建产生的新证据才决定结果。

### 命令开始前失败

```text
Adapter 预检失败
→ EspIdfError
→ run_workflow 的唯一异常边界
→ 安全的 WorkflowError(stage="build")
→ failed
```

这里没有伪造 `return_code=1`，因为命令根本没有运行。相反，命令已经启动
但超时会形成 `return_code=-1` 的 `BuildEvidence`。

## 四、subprocess.run 的关键语法

```python
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
```

- `[*self.idf_command, action]` 用 `*` 展开已有命令，再追加动作；最终是参数列表。
- `shell=False` 表示不让 PowerShell/CMD 再解释字符串，缩小命令注入风险。
- `cwd=root` 固定工程根目录；不能让模型决定任意工作目录。
- `capture_output=True` 同时捕获 stdout 和 stderr。
- `text=True` 把输出解码成字符串；非法字节用替换字符处理。
- `check=False` 让 Adapter 自己把非零返回码转换成领域证据。
- `timeout=...` 为 reconfigure 和 build 设置各自的时间预算。
- `env=environment` 传入环境副本，不修改整个 Python 进程的 `os.environ`。

`CompletedProcess.returncode` 是工具事实，不是 Python 语法检查。pytest 测试
通过代表测试实际调用了测试函数并且所有断言成立；普通 `class`/`def` 只有在
被节点、Adapter 或测试调用时才执行函数体。

## 五、默认禁止下载为什么是权限边界

Adapter 会扫描项目自有的 `idf_component.yml`。只要其中存在非空
`dependencies`，且应用没有显式传入 `allow_dependency_downloads=True`，
预检就在任何 `idf.py` 命令启动前失败；安全模式还向子进程写入：

```text
IDF_COMPONENT_MANAGER=0
```

这和 Prompt 中写“不要下载”不是同一级别：Prompt 只是给模型的说明，可能被
忽略；构造参数、清单验证和进程环境是确定性代码执行的强制规则。授权只能由
应用装配层给出，用户任务文本、模型、State 都不能扩大权限。

`managed_components` 和 `dependencies.lock` 属于 ESP-IDF 工具，不交给模型
读取或修改。清单扫描还排除构建目录、隐藏目录、编辑器目录、符号链接和
Windows Junction，并限制单文件及总读取字节数。

## 六、怎样把终端文字变成可修复证据

处理顺序是：

```text
真实 stdout/stderr
→ 按稳定优先级分类
→ 用编译器/CMake 固定格式提取 BuildDiagnostic
→ 删除 ANSI 控制符
→ 项目内路径改为相对路径
→ 项目外绝对路径改为 <external-path>
→ 截断到固定长度
→ 写入 BuildEvidence
```

失败分类优先级为：`dependency → environment → linker → source → unknown`。
只有 `source` 和 `linker` 可以进入源码修复；`dependency`、`environment`、
`unknown` 直接失败；`timeout` 只在尝试预算内重试。这样不会让 LLM 用修改
源码的方式“修复”缺少编译器或未授权依赖。

正则表达式在这里不负责理解程序意图，只负责把类似
`main/main.c:42:17: error: ...` 的固定文本拆成字段。真正如何改代码，才交给
DeepSeekRepairPlanner；路径包含、文件类型、大小和只改已有文件仍由 Domain
与 Workspace Adapter 强制验证。

## 七、为什么这仍然是 Agent

如果只有 `idf.py build`，它只是脚本。LUXAR 还会：

1. 用 LLM 把自然语言变成受验证的 Requirement 和 Plan；
2. 根据真实 Evidence 选择完成、重试、修复或失败；
3. 修复时再次调用 LLM，但只接受结构化、受路径约束的方案；
4. 修改后重新调用工具，用新证据闭环验证；
5. 用 LangGraph 保存状态、执行条件路由并限制循环预算。

所以它可以理解成“有 LLM 能力、工具证据和安全边界的受约束状态机”。

## 八、测试层次

- Adapter 单元测试 monkeypatch `subprocess.run`，离线验证每个参数、阶段、超时、分类和脱敏。
- Runner/Bootstrap 测试验证异常收口与真实 Adapter 注入，仍不运行外部命令。
- Graph 集成测试使用 Fakes 验证七节点完整闭环。
- 真实 smoke 只有同时设置 `LUXAR_RUN_ESPIDF_SMOKE=1` 且当前环境能找到
  `idf.py` 时才运行，并只在 pytest 临时目录创建无依赖最小工程。

默认完整测试不会安装 ESP-IDF、下载组件、调用 DeepSeek 或修改用户工程。

## 九、一页复习检查表

- Port 说“需要什么能力”，Adapter 说“具体怎样实现”。
- `RuntimeContext.espidf` 是注入的对象，不是 LangGraph 自带的一长串方法。
- 预检失败用 `EspIdfError`；命令已执行的事实用 `BuildEvidence`。
- 参数列表加 `shell=False`，工作目录、环境、超时都由可信应用代码控制。
- 默认下载禁令是构造配置和进程环境的双重强制，不是自然语言约定。
- LLM 负责语义转换和修复推理；确定性代码负责权限、路径、分类、验证与路由。
- 编译成功必须由真实返回码和证据证明，不能由模型宣称。

---

## 十、逐行教学一：对象如何从 Bootstrap 到达真实 `idf.py`

这一节回答：`build_project` 没有导入 `EspIdfCliAdapter`，为什么仍然能执行
真实构建？

### 10.1 完整对象链

```text
bootstrap 创建 EspIdfCliAdapter
→ 保存到 RuntimeContext.espidf
→ LangGraph 调用 build_project(state, runtime)
→ 节点取得 runtime.context.espidf
→ 调用 espidf.build(project_path)
→ 真实对象执行 EspIdfCliAdapter.build()
→ 返回 BuildEvidence
→ 节点把 Evidence 写入 WorkflowState
→ route_after_build 根据 Evidence 路由
```

在 `bootstrap.py` 中：

```python
if espidf is None:
    espidf = EspIdfCliAdapter(
        idf_command=idf_command,
        allow_dependency_downloads=allow_dependency_downloads,
    )
```

正式运行没有传入 `espidf` 时，组合根创建真实 Adapter。测试传入
`FakeEspIdf` 时，`espidf is None` 为假，因此保留 Fake。

随后把对象放入 Context：

```python
return RuntimeContext(
    requirement_parser=requirement_parser,
    planner=planner,
    repair_planner=repair_planner,
    espidf=espidf,
    workspace=workspace,
    project_path=project_path,
)
```

`RuntimeContext` 的声明是：

```python
@dataclass(frozen=True)
class RuntimeContext:
    espidf: EspIdfPort
    project_path: Path
```

`frozen=True` 防止工作流运行中意外把工具换掉。`espidf: EspIdfPort` 是类型
合同，不要求对象必须继承某个基类；对象只要提供兼容的 `build()` 方法即可。

Port 的合同为：

```python
class EspIdfPort(Protocol):
    def build(self, project_path: Path) -> BuildEvidence:
        ...
```

因此下面两个对象都可以放进去：

```text
EspIdfCliAdapter.build(Path) → BuildEvidence
FakeEspIdf.build(Path)       → BuildEvidence
```

节点代码：

```python
def build_project(
    state: WorkflowState,
    runtime: Runtime[RuntimeContext],
) -> dict[str, object]:
    espidf = runtime.context.espidf
    project_path = runtime.context.project_path
    evidence = espidf.build(project_path)
```

`Runtime[...]` 是 LangGraph 的包装类型；方括号中的 `RuntimeContext` 是我们
自己的类型参数。可以把多层访问画成：

```text
runtime                       LangGraph 传入的 Runtime 对象
└─ context                    Runtime 自带的 context 属性
   └─ espidf                  我们在 RuntimeContext 中声明的字段
      └─ build(project_path)  实际注入对象提供的方法
```

所以 `espidf` 不是 LangGraph 自带的能力。LangGraph 只负责把 `context` 送到
节点；Context 里面装哪些对象由 LUXAR 的组合根决定。

节点收到 Evidence 后返回最小 State 更新：

```python
return {
    "build_evidence": evidence,
    "attempts": state.get("attempts", 0) + 1,
    "status": "building",
    "trace": [*state.get("trace", []), "build_project"],
}
```

LangGraph 合并更新后调用 `route_after_build()`：成功进入 `completed`；
source/linker 进入 `repair_project`；timeout 在预算内重新进入 `build_project`；
dependency/environment/unknown 进入 `failed`。

这里的关键设计是依赖倒置：节点依赖稳定 Port，Bootstrap 才依赖具体 Adapter。
更换 CLI 实现或使用 Fake 都不需要修改节点和 Graph。

## 十一、逐行教学二：`_preflight()` 前置检查

`preflight` 可以理解为“起飞前检查”。它解决的不是如何编译，而是：

> 在产生任何真实工程副作用前，当前项目、命令、权限和环境是否可信？

函数签名：

```python
def _preflight(
    self,
    project_path: Path,
) -> tuple[Path, dict[str, str]]:
```

返回一个二元组：验证后的项目根 `Path`，以及交给子进程的环境变量字典。

### 11.1 验证工程根和命令

```python
root = _resolve_project_root(project_path)
self._validate_command()
```

根目录必须存在、必须是目录、不能是 symlink/Junction，并且根
`CMakeLists.txt` 必须是普通文件。`resolve(strict=True)` 得到真实存在的规范
路径。绝对路径只在 Adapter 内部作为可信 `cwd` 和路径包含判断，不直接进入
State。

默认命令前缀是单元素元组：

```python
idf_command = ("idf.py",)
```

单元素元组必须有尾部逗号；`("idf.py")` 只是字符串。相对 launcher 通过
`shutil.which()` 在当前 `PATH` 中查找；显式绝对 launcher 则必须是现有普通
文件。失败产生稳定脱敏的 `EspIdfError(category="environment")`。

### 11.2 发现并读取 Manifest

```python
manifests = _discover_manifests(root)
```

递归搜索精确名称 `idf_component.yml`，但不进入 `.git`、编辑器目录、隐藏
目录、`build`、`build_*`、`managed_components` 和 `__pycache__`。发现顺序
按项目相对 POSIX 路径排序，保证相同输入得到相同处理顺序。

```python
for manifest in manifests:
    loaded, actual_bytes = self._read_manifest(manifest)
    total_bytes += actual_bytes
```

`loaded, actual_bytes = ...` 是元组拆包。读取函数在读取前后检查单文件大小，
拒绝 NUL、非 UTF-8 和无效 YAML，并返回真正读取的字节数。总预算也按实际
字节累加，而不是按字符数或不可靠的预估值。

```python
loaded = yaml.safe_load(text)
```

YAML 解析后的类型仍需检查。合法 YAML 不等于合法 ESP-IDF 清单；列表、非
mapping 的 `dependencies` 等结构会被拒绝。不能改用不安全的 `yaml.load()`。

### 11.3 汇总多个清单中的依赖

```python
has_declared_dependencies = (
    _manifest_has_dependencies(loaded)
    or has_declared_dependencies
)
```

项目可能有多个组件清单。只要任意清单出现非空 `dependencies`，变量就保持
`True`。它等价于更长的：

```python
if _manifest_has_dependencies(loaded):
    has_declared_dependencies = True
```

### 11.4 权限检查与环境副本

```python
if has_declared_dependencies and not self.allow_dependency_downloads:
    raise EspIdfError(category="dependency", ...)
```

“存在依赖”和“没有授权”同时成立时，在任何 `idf.py` 命令之前失败。授权只能
由应用装配参数提供，不能来自 task text、Prompt、模型响应或 State。

```python
environment = os.environ.copy()
```

复制环境是为了不修改当前 Python 进程的全局 `os.environ`。安全模式设置：

```python
environment["IDF_COMPONENT_MANAGER"] = "0"
```

显式授权时则删除复制环境中可能继承的禁用值：

```python
environment.pop("IDF_COMPONENT_MANAGER", None)
```

`pop` 的第二个参数 `None` 表示键不存在时不报错。

这里形成两层确定性防护：扫描清单提前给出明确错误，子进程环境进一步关闭
Component Manager。两层都由代码强制执行，比只在 Prompt 里提醒更可靠。

最后：

```python
return root, environment
```

调用者使用：

```python
root, environment = self._preflight(project_path)
```

只有拿到这两个返回值，才会进入真实命令阶段。

## 十二、逐行教学三：`_run_action()` 与执行结果

`_preflight()` 判断命令能否开始；`_run_action()` 记录开始后实际发生了什么。

```python
def _run_action(
    self,
    *,
    action: Literal["reconfigure", "build"],
    root: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> BuildEvidence:
```

独立的 `*` 表示后续参数必须使用名称传入，减少路径、动作和超时位置传错的
风险。`Literal` 向编辑器和类型检查器声明合法动作只有两个。

### 12.1 真正启动进程

```python
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
```

若命令前缀为 `("idf.py",)`、动作为 `"build"`，列表展开结果就是
`["idf.py", "build"]`。`shell=False` 不让 PowerShell/CMD 二次解释命令
字符串；`cwd` 固定工程根；`capture_output` 捕获 stdout/stderr；`check=False`
允许 Adapter 自己处理非零返回码。

命令正常结束会返回 `CompletedProcess`。即使编译失败，它仍可能“正常结束”：

```text
进程成功启动并退出 ≠ 工程构建成功
```

工程结果由 `result.returncode` 判断：0 成功，非零失败。`result.stdout` 和
`result.stderr` 是两个输出通道；真实工具不保证诊断只出现在 stderr，所以
分类和解析同时读取二者。

### 12.2 三类运行结果

普通成功：

```python
BuildEvidence(
    success=True,
    command=["idf.py", action],
    return_code=0,
)
```

普通失败：进程结束并给出非零返回码，Adapter 分类输出、解析诊断，返回
`success=False` 的 Evidence。

超时：

```python
except subprocess.TimeoutExpired as error:
```

进程已经启动却没有按时结束，因此仍然存在可观察的执行事实，使用
`BuildEvidence(return_code=-1, error_category="timeout")`。异常中的输出可能
是 `str`、`bytes` 或 `None`，需要先统一成字符串并脱敏。

启动失败：

```python
except OSError as error:
    raise EspIdfError(category="process", ...) from error
```

进程根本没有成功启动，因此不能伪造 BuildEvidence。`raise ... from error`
保留内部调试因果链，但对外只暴露稳定消息。

边界可以记为：

| 事实 | 表达方式 |
|---|---|
| 项目或命令预检失败 | `EspIdfError` |
| 进程无法启动 | `EspIdfError` |
| 进程返回 0 | 成功 `BuildEvidence` |
| 进程返回非 0 | 失败 `BuildEvidence` |
| 进程启动后超时 | timeout `BuildEvidence` |

`_logical_command()` 只保存 `["idf.py", action]`，不把本机 Python、ESP-IDF
安装目录等绝对路径写入 State。

## 十三、逐行教学四：日志分类与诊断解析

终端输出本质上只是字符串。LangGraph 不能可靠地直接根据几千行文字路由，
RepairPlanner 也更适合接收“文件、行号、错误信息”这类结构化数据。因此
Adapter 要把原始日志转换为两种信息：

```text
error_category  → 决定是否修复、重试或失败
diagnostics     → 告诉修复模型具体文件和位置
```

### 13.1 为什么这里不用 LLM 分类

这些判断具有稳定工程规则：返回码、编译器格式、明确错误短语、权限策略。
如果每次都问 LLM，会增加网络开销、随机性和把日志中的恶意文本当指令的风险。

分工是：

```text
确定性代码：判断失败类别、提取固定格式字段、执行权限和路由
LLM：结合 Requirement、Plan、源码和结构化 Diagnostic 推理怎样改代码
```

### 13.2 `_classify_failure()`

```python
combined = f"{stdout}\n{stderr}".casefold()
```

f-string 把两个输出拼接；`casefold()` 生成适合不区分大小写比较的文本，比单纯
`lower()` 更面向 Unicode。随后按固定优先级检查：

```python
if any(signal in combined for signal in _DEPENDENCY_SIGNALS):
    return "dependency"
```

这里同时出现了三个语法：

- `for signal in ...` 逐个取出已知短语；
- `signal in combined` 判断短语是否存在；
- `any(...)` 只要任意一个判断为真就返回真。

优先级为：

```text
dependency → environment → linker → source → unknown
```

顺序很重要。例如依赖解析失败的日志也可能包含 `CMake Error`；如果先判断
CMake，就可能把权限/依赖问题错误送去修改源码。链接器日志也常包含普通
`error`，所以 linker 必须在 source 之前。

源码判断没有使用模糊的单词 `error`，而是逐行要求匹配 GCC/Clang 诊断格式：

```python
if any(
    _GCC_DIAGNOSTIC_RE.match(line)
    for line in _strip_ansi(f"{stdout}\n{stderr}").splitlines()
):
    return "source"
```

`splitlines()` 拆成行；括号里的表达式是生成器表达式，不会先创建整个布尔
列表；`any()` 找到首个匹配即可停止。实在没有可信模式才返回 `unknown`，不
猜测为可修复错误。

### 13.3 GCC/Clang 正则怎样拆字段

典型输入：

```text
C:\project\main\main.c:42:17: error: expected ';'
```

正则使用命名捕获组，概念上拆成：

```text
file     = C:\project\main\main.c
line     = 42
column   = 17
severity = error
message  = expected ';'
```

读取字段：

```python
gcc_match.group("file")
gcc_match.group("line")
```

正则得到的数字仍是字符串，因此转换：

```python
line=int(gcc_match.group("line"))
```

可选列号使用条件表达式：

```python
column=(
    int(gcc_match.group("column"))
    if gcc_match.group("column") is not None
    else None
)
```

它可以读成：“有 column 就转成整数，否则保存 None”。`fatal error` 也规范
成领域模型允许的 `severity="error"`。

### 13.4 CMake 诊断为什么读取下一条非空行

CMake 常输出：

```text
CMake Error at main/CMakeLists.txt:12 (idf_component_register):
  Component requirement was not found
```

第一行给出文件和行号，具体说明在后续行。因此代码先给稳定默认消息，然后
向后寻找第一条非空说明：

```python
message = "CMake configuration error"
for next_line in lines[index + 1:]:
    if next_line.strip():
        message = next_line.strip()
        break
```

`lines[index + 1:]` 是列表切片，表示当前行之后的所有行；`strip()` 去除首尾
空白；`break` 找到第一条后停止循环。

### 13.5 为什么诊断路径可能变成 `None`

```python
file=_path_inside_project(raw_file, root)
```

项目内绝对路径转换成相对 POSIX 路径，例如 `main/main.c`。相对路径包含
`..`，或者绝对路径位于项目外，则返回 `None`。

保留行号和消息、隐藏外部文件名，比把整条诊断丢掉更有价值：模型仍知道发生
了什么，但不会得到用户目录或工具链安装路径。这个函数只做词法判断，不根据
日志中的路径读取文件。

### 13.6 去重为什么同时需要 list 和 set

编译日志可能重复同一诊断。代码使用：

```python
diagnostics: list[BuildDiagnostic] = []
seen: set[tuple[object, ...]] = set()
```

`list` 保持首次出现顺序，`set` 快速判断是否已经出现。每条诊断的字段组成
不可变 tuple 作为 key：

```python
if key not in seen:
    seen.add(key)
    diagnostics.append(diagnostic)
```

只用 set 会失去明确的输出顺序；只用 list 查重则每次都要线性比较。

### 13.7 `_sanitize_output()` 和结构化诊断的区别

`diagnostics` 是给程序和修复模型使用的结构化字段；`stdout_summary`、
`stderr_summary` 是给人和模型补充上下文的有限文本。

文本清理顺序：

```text
移除 ANSI 控制符
→ CRLF/CR 统一为 LF
→ 删除项目根绝对前缀
→ Windows 分隔符统一为 /
→ 其他绝对路径替换为 <external-path>
→ 截断到 max_summary_chars
```

它不会修改原始编译器进程，只决定什么信息可以进入 State。当前切片不持久化
完整原始日志。

最终失败 Evidence 同时带有：

```python
BuildEvidence(
    success=False,
    return_code=result.returncode,
    error_category=_classify_failure(...),
    diagnostics=_parse_diagnostics(...),
    stdout_summary=stdout_summary,
    stderr_summary=stderr_summary,
)
```

到这里，终端字符串已经变成 LangGraph 能稳定路由、RepairPlanner 能安全使用
的领域事实。

## 十四、逐行教学五：`EspIdfError` 如何到达 Runner

这一节回答四个问题：

1. Adapter 抛出的异常为什么能越过 LangGraph 节点？
2. 为什么不在每个节点分别捕获？
3. Runner 怎样保留已经完成的 Requirement 和 Plan？
4. 为什么预检失败后没有 Evidence、attempts 增量和 `build_project` trace？

### 14.1 两种“错误对象”不是同一个职责

命令开始前，Adapter 可能抛出：

```python
class EspIdfError(RuntimeError):
    ...
```

它是 Python 异常，表示控制流无法继续正常返回。它保存的是 Port 层稳定事实：

```text
category  → invalid_project / environment / dependency / process
message   → Adapter 内部的稳定消息
retryable → 底层能力是否适合重试
```

Runner 最终写入 State 的则是：

```python
class WorkflowError(BaseModel):
    stage: ...
    category: ...
    message: str
    retryable: bool
    user_suggestion: str
```

它不是正在传播的 Python 异常，而是经过验证、可以安全保存和展示的领域数据。

可以记成：

```text
EspIdfError  → “当前 Python 调用不能正常完成”
WorkflowError → “工作流最终怎样记录和解释失败”
```

### 14.2 异常为什么能越过节点

构建节点的核心代码是：

```python
evidence = espidf.build(project_path)

next_attempt = state.get("attempts", 0) + 1

return {
    "build_evidence": evidence,
    "attempts": next_attempt,
    "status": "building",
    "trace": [*state.get("trace", []), "build_project"],
}
```

节点内没有捕获 `EspIdfError`。如果 `_preflight()` 在第一行调用期间抛出异常，
Python 会立即停止当前函数，并沿调用栈向外寻找能够处理该异常的 `except`。

因此下面三部分都不会执行：

```text
next_attempt 的计算
节点 return 字典
LangGraph 合并本节点的 State 更新
```

调用栈可以画成：

```text
run_workflow()
└─ graph.stream()
   └─ build_project()
      └─ espidf.build()
         └─ _preflight()
            └─ raise EspIdfError
                 ↑ 逐层退出，直到 run_workflow 的 except
```

这里的“越过 LangGraph”不是绕过 Graph，而是普通 Python 异常传播：中间函数
没有捕获，就继续向外传播。

### 14.3 为什么只有 Runner 统一捕获一次

Runner 使用联合异常捕获：

```python
except (CapabilityError, WorkspaceError, EspIdfError) as error:
```

圆括号中的 tuple 是“允许捕获的异常类型集合”，表示以下任意一种都进入同一
边界：

```text
CapabilityError → DeepSeek/模型能力失败
WorkspaceError  → 工作区读写或安全验证失败
EspIdfError     → ESP-IDF 命令开始前的能力失败
```

统一边界有三个好处：

1. 节点保持业务编排简洁，不重复错误转换代码；
2. 所有底层敏感消息都在同一位置换成应用控制的安全文字；
3. 以后 API、CLI 或 UI 只需要理解统一的失败 State。

进入联合 `except` 后，再用运行时类型分支选择转换函数：

```python
if isinstance(error, CapabilityError):
    ...
elif isinstance(error, WorkspaceError):
    ...
else:
    workflow_error = espidf_error_to_workflow_error(error)
```

`isinstance(object, Class)` 在运行时判断对象是不是某个类的实例。因为 tuple 中
只可能出现三种已知异常，前两种排除后，`else` 就是 `EspIdfError`。

### 14.4 为什么不能把原始异常消息直接写入 State

ESP-IDF 转换函数使用固定字典：

```python
ESPIDF_ERROR_MESSAGES = {
    "invalid_project": "ESP-IDF 项目结构无效",
    "environment": "ESP-IDF 构建环境不可用",
    "dependency": "项目依赖需要显式授权后才能解析",
    "process": "ESP-IDF 构建进程无法启动",
}
```

然后：

```python
message = ESPIDF_ERROR_MESSAGES[error.category]
```

而不是：

```python
message = error.message
```

因为底层异常将来可能意外包含用户目录、命令路径、系统消息或依赖内容。即使
Adapter 已经尽量使用稳定文字，Application 边界仍不信任它的原始 message。

测试故意构造：

```python
EspIdfError(
    category="dependency",
    message="SECRET_ESPIDF_PATH",
    retryable=False,
)
```

然后断言敏感标记不会出现在最终 `WorkflowError.message` 或
`user_suggestion`。这就是纵深防御：Adapter 做一次脱敏，Runner 再做一次固定
映射。

### 14.5 为什么四个底层类别只映射成两个工作流类别

```python
category = (
    "dependency"
    if error.category == "dependency"
    else "environment"
)
```

这是 Python 条件表达式，可以读成：

```text
如果底层类别是 dependency：工作流类别使用 dependency；
否则：工作流类别使用 environment。
```

映射关系为：

| `EspIdfError.category` | `WorkflowError.category` |
|---|---|
| `invalid_project` | `environment` |
| `environment` | `environment` |
| `dependency` | `dependency` |
| `process` | `environment` |

Port 层类别更细，方便 Adapter 表达原因和选择消息；工作流层只保留路由、用户
提示和恢复真正需要的稳定类别。所有这些失败的阶段都固定是：

```python
stage="build"
```

因为它们发生在 ESP-IDF 构建能力中，而不是需求分析或修复阶段。

### 14.6 `model_validate()` 在转换末尾做什么

```python
return WorkflowError.model_validate(
    {
        "stage": "build",
        "category": category,
        "message": ESPIDF_ERROR_MESSAGES[error.category],
        "retryable": error.retryable,
        "user_suggestion": ESPIDF_ERROR_SUGGESTIONS[error.category],
    }
)
```

普通字典先由 Application 组装，再交给 Pydantic 创建 `WorkflowError`。如果
stage、category 或字段类型违反领域模型的 `Literal` 和类型声明，验证会立即
失败，错误对象不会以不合法形态进入 State。

### 14.7 Runner 为什么使用 `stream()` 而不是只用 `invoke()`

Runner 开始时复制初始 State：

```python
latest_state = cast(
    WorkflowState,
    dict(initial_state),
)
```

`dict(initial_state)` 创建一个浅复制。这样即使第一个节点立即失败，原始
`task_text`、attempts 和 max_attempts 仍然存在。

随后：

```python
for snapshot in build_graph().stream(
    initial_state,
    context=context,
    stream_mode="values",
):
    latest_state = cast(WorkflowState, snapshot)
```

`stream_mode="values"` 让 LangGraph 在每个节点成功完成后给出一份完整 State
快照。循环每次把 `latest_state` 更新为最新快照。

例如构建预检失败前：

```text
初始 State
→ analyze_requirement 成功：snapshot 含 Requirement
→ create_plan 成功：snapshot 含 Requirement + Plan
→ build_project 内抛异常：没有本节点 snapshot
```

因此 Runner 捕获异常时，`latest_state` 正好是“计划节点完成后的最后可信
状态”。

如果只调用一次普通执行并在中途抛异常，外层未必方便获得已经完成节点后的
最新 State；使用 values stream 明确提供了这个应用级恢复边界。

### 14.8 `cast()` 会不会转换字典

```python
latest_state = cast(WorkflowState, snapshot)
```

`typing.cast()` 不会在运行时复制、验证或转换对象。它只是告诉类型检查器：

> 请把这个对象视为 `WorkflowState`。

运行时的 `snapshot` 仍然是原来的 dict。真正的数据约束主要来自节点设计、
Pydantic Domain 对象以及测试，而不是 `cast()`。

### 14.9 怎样组合最终失败 State

Runner 复用已有终态节点：

```python
failure_update = failed(latest_state)
```

`failed()` 只返回：

```python
{
    "status": "failed",
    "trace": [*state.get("trace", []), "failed"],
}
```

最终组合：

```python
return cast(
    WorkflowState,
    {
        **latest_state,
        "error": workflow_error,
        **failure_update,
    },
)
```

两个 `**` 是字典展开。合并顺序很重要：

1. 先复制最后可信 State；
2. 加入转换后的安全 error；
3. 用 failed 节点更新 status 和 trace。

原有 Requirement、Plan 等未被同名键覆盖，因此继续保留。

### 14.10 为什么没有 Evidence、attempts 增量和构建 trace

预检在这句内部失败：

```python
evidence = espidf.build(project_path)
```

所以节点没有执行完整，更没有返回：

```python
"build_evidence": evidence
"attempts": next_attempt
"trace": [..., "build_project"]
```

最终测试期望：

```text
requirement 已保留
plan 已保留
没有 build_evidence
attempts 仍是 0
status 是 failed
error.stage 是 build
error.category 是 dependency
trace 是 analyze_requirement → create_plan → failed
```

这不是信息丢失，而是忠实表达事实：

```text
需求分析完成了
执行计划完成了
构建命令没有开始
所以没有构建证据，也没有一次实际构建尝试
```

### 14.11 与“命令返回 dependency Evidence”的区别

有两种看起来相似但事实不同的依赖失败：

```text
清单预检发现未授权依赖
→ 命令没启动
→ EspIdfError(dependency)
→ Runner 生成失败 State
→ attempts 不增加，没有 BuildEvidence
```

```text
显式授权后命令已启动，但 Component Manager 解析失败
→ 进程非零退出
→ BuildEvidence(error_category="dependency")
→ build_project 正常返回
→ attempts 增加，有 BuildEvidence
→ route_after_build 进入 failed
```

最终都失败，但 State 中保存的事实不同。企业项目尤其需要这种区分，否则审计
时无法判断命令到底有没有真正执行。

### 14.12 本节记忆模型

```text
Adapter 抛异常
→ 当前节点立即停止，不能伪造节点更新
→ Python 沿调用栈把异常交给 Runner
→ Runner 用固定字典转换成安全 WorkflowError
→ values stream 提供最后成功节点后的 State
→ 合并 error 与 failed 更新
→ 返回可展示、可测试、可持久化的失败 State
```

最核心的一句话：

> Runner 保存的是“已经成功发生的事实”，而不是为了让 State 看起来完整而补造
> 尚未发生的 Evidence、attempt 或 trace。
