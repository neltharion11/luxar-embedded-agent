# 10：LUXAR CLI 入口与安全进度

这一章从最终代码解释：一条终端命令怎样变成 LangGraph 的初始 State，工作流
怎样把安全进度送回终端，最后怎样变成中文摘要、JSON 和进程退出码。

## 一、完整纵向链路

```text
用户输入 luxar run ...
→ 操作系统执行 luxar.exe
→ setuptools 映射到 luxar.cli:main
→ argparse 解析 argv
→ CLI 验证 project/task/max-attempts
→ Bootstrap 创建 RuntimeContext
→ CLI 创建初始 WorkflowState
→ Runner stream 执行七节点 LangGraph
→ Runner 产生有限 WorkflowProgress
→ CLI 把 progress 写入 stderr
→ Runner 返回最终 WorkflowState
→ CLI 输出中文摘要或 JSON 到 stdout
→ main 返回 0/2/3/4/130
→ 操作系统得到进程退出码
```

CLI 是 Presentation Adapter（展示适配器）：它把人和脚本的输入转换成应用调用，
再把应用结果转换成终端输出。它不实现 DeepSeek、文件修改、`idf.py` 或 Graph。

## 二、英文名词对照

| 英文 | 中文 | 当前含义 |
|---|---|---|
| CLI | 命令行接口 | 通过终端参数运行 LUXAR |
| Shell | 命令解释器 | PowerShell/CMD，负责启动 `luxar.exe` |
| argv | 参数向量 | 传给程序的字符串参数序列 |
| Parser | 解析器 | `argparse` 将字符串转成具名值 |
| Subcommand | 子命令 | 当前的 `run` |
| Option | 选项 | `--project PATH`、`--task TEXT` |
| Flag | 开关 | 出现即为真的 `--json`、下载授权 |
| stdin | 标准输入 | `input()` 交互读取 task |
| stdout | 标准输出 | 最终中文摘要或 JSON |
| stderr | 标准错误 | 进度和启动错误 |
| Exit Code | 退出码 | 操作系统看到的整数结果 |
| Callback | 回调 | Runner 在阶段完成时调用的 reporter |
| Serialization | 序列化 | 把 Domain 对象转换成 JSON 数据 |
| Presentation Adapter | 展示适配器 | 连接终端表现与 Application 的边界 |

## 三、命令为什么会进入 `main()`

`pyproject.toml` 注册：

```toml
[project.scripts]
luxar = "luxar.cli:main"
```

editable install 后，安装工具生成 `luxar.exe`。冒号左边是 Python 模块
`luxar.cli`，右边是函数 `main`。因此执行 `luxar ...` 最终调用：

```python
main(argv=None)
```

`None` 表示 argparse 读取真实 `sys.argv`。测试可以显式传入 list，例如：

```python
main(["run", "--project", "project", "--task", "build"])
```

这样不需要修改整个测试进程的命令行。

## 四、argparse 怎样建立命令结构

```python
parser = argparse.ArgumentParser(...)
subcommands = parser.add_subparsers(dest="command", required=True)
run_parser = subcommands.add_parser("run")
```

然后注册 `--project`、`--task`、`--max-attempts`、下载授权和 `--json`。

`action="store_true"` 表示 flag 不需要值：未出现是 False，出现是 True。正整数
解析器先 `int(value)`，再拒绝小于等于零；无效参数由标准 argparse 打印 usage
并抛出 `SystemExit(2)`。

## 五、参数什么时候才成为 State

终端参数本身不是 LangGraph State。CLI 显式完成转换：

```python
initial_state = WorkflowState(
    task_text=task,
    attempts=0,
    max_attempts=args.max_attempts,
    trace=[],
)
```

项目路径和下载授权没有进入 State：

```python
context = build_deepseek_runtime_context(
    project_path=project,
    allow_dependency_downloads=args.allow_dependency_downloads,
)
```

项目路径属于 RuntimeContext；权限属于 Adapter 构造配置。这样它们不会因为
LangGraph State 的日志或未来 checkpoint 被当成业务进展持久化。

`--project` 必须显式提供，不猜当前目录。普通模式可以用 `input()` 补 task；
JSON 模式缺 task 立即返回 2，避免自动化脚本挂起等待输入。

## 六、为什么 Progress 不是 State 快照

Runner 定义冻结对象：

```python
@dataclass(frozen=True)
class WorkflowProgress:
    stage: ProgressStage
    message: str
    attempts: int
```

它只含固定阶段、固定中文文字和次数，不含 task、项目路径、源码、Prompt、日志、
密钥或完整 State。

Runner 从每个 `stream_mode="values"` 快照的最后一个 trace 项推导事件。CLI 提供
callback：

```python
progress_reporter=_report_progress
```

Runner 在阶段完成时调用它。JSON 模式传 `None`，所以不会出现进度污染。

能力异常只在 `next(stream)` 周围捕获；reporter 在捕获区外调用。因此 reporter
自身的编程错误不会被伪装成 DeepSeek 或工具失败。

## 七、stdout 与 stderr 为什么分开

普通模式：

```text
stderr → [需求]、[计划]、[构建] 等动态进度
stdout → 最终中文摘要
```

JSON 模式：

```text
stdout → 唯一一份最终 JSON
stderr → 只用于参数/启动错误
```

脚本通常只解析 stdout。若进度混入 JSON，`json.loads()` 就会失败。业务 failed
State 仍然是成功生成的机器结果，因此 JSON 写 stdout，但进程返回 4。

## 八、稳定 JSON 外壳

CLI 不直接序列化整个 State，而是建立允许列表：status、exit_code、attempts、
requirement、plan、build_evidence、repair_plan、changed_files、error 和 trace。

Pydantic 对象调用：

```python
model_dump(mode="json")
```

缺失对象输出 `null`，列表默认 `[]`。task_text、project、Context、Settings、Client
和 API key 永不进入 JSON。Evidence 中的摘要可以输出，因为它已通过 Adapter
的路径脱敏和长度限制。

## 九、退出码

| 代码 | 含义 |
|---:|---|
| 0 | completed |
| 2 | 参数或启动配置错误 |
| 3 | needs_clarification |
| 4 | workflow failed |
| 130 | Ctrl+C 取消 |

退出码适合 CI 快速分流，JSON 里的 `exit_code` 适合只保存文档的调用者。未知
编程错误不会被宽泛 `except Exception` 隐藏。

## 十、测试真正执行了什么

CLI 测试调用真实 `main([...])` 和真实 argparse，但 monkeypatch Bootstrap 与
Runner，所以不调用 DeepSeek 或 ESP-IDF。`capsys` 分别捕获 stdout/stderr，
验证流没有混淆；`json.loads()` 证明 stdout 是一份有效 JSON。

Runner 测试执行真实 Graph 和 Fake 外部能力，证明进度顺序与异常边界。安装
验证执行真实 `luxar.exe --help`，证明 setuptools console script 已生成，但
help 不创建 RuntimeContext，所以不读取 API key 或运行工具。

## 十一、实际使用

激活环境后：

```powershell
luxar run --project C:\projects\blink --task "修复 ESP32 GPIO 工程"
```

未激活时可以使用完整路径：

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\Scripts\luxar.exe run `
  --project C:\projects\blink `
  --task "修复 ESP32 GPIO 工程"
```

真实运行仍需要正确的 `DEEPSEEK_API_KEY` 和已激活的 ESP-IDF 环境。只有确认
依赖来源后才添加 `--allow-dependency-downloads`。

## 十二、记忆模型

```text
CLI 负责“怎么输入、怎么展示”
Bootstrap 负责“本次使用哪些真实对象”
Runner 负责“怎样执行 Graph 并收口能力异常”
LangGraph 负责“State 怎样流动和路由”
Adapter 负责“怎样调用真实外部能力”
Domain 负责“什么数据才合法”
```

CLI 让完整 Agent 真正可用，但它没有成为新的业务核心。

---

## 十三、逐行教学一：PowerShell 怎样调用到 `main()`

这一节只解决一条调用链：

```text
PowerShell 中输入 luxar run ...
→ Windows 找到 luxar.exe
→ 启动器加载 Python 环境
→ 导入 luxar.cli
→ 找到 main 函数
→ 调用 main()
→ main 的返回整数成为进程退出码
```

### 13.1 Shell、命令和 Python 不是同一个东西

假设输入：

```powershell
luxar run --project C:\projects\blink --task "修复 GPIO 工程"
```

PowerShell 是 Shell（命令解释器）。它首先做的是解析终端命令，而不是导入
Python：

```text
程序名称：luxar
后续参数：run、--project、C:\projects\blink、--task、修复 GPIO 工程
```

引号的作用是让包含空格的任务文字成为一个参数。PowerShell 查找名为
`luxar`、`luxar.exe` 等可执行文件；找到后由 Windows 创建新进程。

因此 `luxar` 不是 Python 关键字，也不是 LangGraph 方法。它是安装到 Conda
环境 `Scripts` 目录中的 console-script 启动器。

### 13.2 `luxar.exe` 从哪里来

项目在 `pyproject.toml` 中声明：

```toml
[project.scripts]
luxar = "luxar.cli:main"
```

三部分含义：

```text
luxar       → 安装后暴露的命令名
luxar.cli   → 要导入的 Python 模块
main        → 模块中要调用的函数
```

冒号不是 Python 对象的属性访问语法，而是打包配置中分隔“模块”和“函数”的
entry-point 表示法。

执行：

```powershell
python -m pip install -e .
```

时，pip 读取 `pyproject.toml`，安装 editable package，并根据
`[project.scripts]` 生成启动器。当前环境实际存在：

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\Scripts\luxar.exe
```

安装元数据也实际记录为：

```text
EntryPoint(
    name='luxar',
    value='luxar.cli:main',
    group='console_scripts',
)
```

所以 `luxar.exe` 不是我们用 C/C++ 手写的业务程序，也不是把整个项目编译成了
机器码。它是打包工具生成的小型启动器，知道应该使用对应 Python 环境并进入
指定函数。

### 13.3 editable install 是什么

`-e` 是 editable（可编辑安装）。普通安装通常把源码复制进
`site-packages`；editable 安装让已安装包指向当前开发源码。

因此修改：

```text
C:\tmp\luxar-langgraph\src\luxar\cli.py
```

后，通常不需要每次复制源码重新安装。但如果改变 `pyproject.toml` 中的
console script 或依赖元数据，仍应重新执行 editable install，让启动器和元数据
同步。

### 13.4 为什么有时能输入 `luxar`，有时必须写完整路径

Shell 通过环境变量 `PATH` 搜索可执行文件。如果已经激活：

```powershell
conda activate luxar-learning
```

Conda 通常把该环境的 `Scripts` 目录临时加入 PATH，于是可以直接输入：

```powershell
luxar --help
```

当前 shell 没有激活该环境时，`Scripts` 可能不在 PATH，就需要：

```powershell
C:\Users\Gugugu\.conda\envs\luxar-learning\Scripts\luxar.exe --help
```

这不是 CLI 代码故障，而是 Shell 是否能找到启动器的问题。可以用下面的思路
排查：

```text
命令找不到
→ 检查 Conda 环境是否激活
→ 检查 Scripts 是否在 PATH
→ 尝试启动器完整路径
```

### 13.5 启动器怎样导入模块

配置中的：

```text
luxar.cli:main
```

概念上相当于启动器执行：

```python
from luxar.cli import main

result = main()
```

真实生成器还有设置解释器和处理退出码等包装逻辑，但业务理解到这一层已经
足够。

导入 `luxar.cli` 时，Python 会从上到下执行模块顶层代码：

```python
import argparse
import json
...
from luxar.application.runner import WorkflowProgress, run_workflow
```

然后创建各个函数对象：

```python
def build_parser(): ...
def _report_progress(...): ...
def main(...): ...
```

定义函数时不会执行函数体。只有启动器调用 `main()` 后，`main` 内部的参数解析、
Bootstrap 和 Runner 才会依次运行。

### 13.6 `main(argv=None)` 为什么能读取真实终端参数

函数签名：

```python
def main(argv: Sequence[str] | None = None) -> int:
```

真实启动器没有传 argv，所以默认值是 `None`：

```python
args = build_parser().parse_args(argv)
```

`argparse.parse_args(None)` 会读取 `sys.argv[1:]`。假设完整系统参数近似为：

```python
sys.argv == [
    ".../luxar.exe",
    "run",
    "--project",
    "C:\\projects\\blink",
    "--task",
    "修复 GPIO 工程",
]
```

索引 0 是程序自身名称，真正业务参数从索引 1 开始。

测试则可以显式调用：

```python
main([
    "run",
    "--project",
    "project",
    "--task",
    "build",
])
```

此时 argparse 使用传入 list，不读取测试进程自己的 `sys.argv`。这就是为什么
`main(argv=...)` 比只能读取全局命令行的写法更容易测试。

### 13.7 `Sequence[str] | None` 怎样理解

从内向外读：

```text
str                    每个参数是字符串
Sequence[str]          一串字符串；list 和 tuple 都可以
Sequence[str] | None   也可以不提供
```

它是类型说明，不会自动解析参数。真正的解析由 `argparse` 执行。

使用 `Sequence` 而不是只写 `list`，允许测试传 list 或 tuple；函数内部也不需要
修改调用者传入的序列。

### 13.8 `--help` 为什么不会调用 DeepSeek 或 ESP-IDF

运行：

```powershell
luxar --help
```

实际输出：

```text
usage: luxar [-h] {run} ...

运行 LUXAR ESP-IDF Agent 工作流
```

`argparse` 识别 `--help` 后打印帮助并通过 `SystemExit(0)` 结束解析。`main()` 不会
继续执行后面的 Bootstrap：

```python
context = build_deepseek_runtime_context(...)
```

因此帮助命令不会读取 `DEEPSEEK_API_KEY`、调用模型或启动 `idf.py`。它验证的
是安装入口和参数结构，不是工作流执行。

### 13.9 `main()` 的返回值怎样成为退出码

`main()` 声明返回 `int`，例如：

```python
return 0
return 2
return 3
return 4
return 130
```

console-script 启动器会把这个整数交给进程退出机制。PowerShell 可以通过：

```powershell
$LASTEXITCODE
```

读取最近外部程序的退出码。

这和 `print()` 完全不同：

```text
print(...)  → 给人或脚本看的文本
return int  → 给操作系统、Shell 或 CI 看的状态
```

即使 JSON 模式已经输出一份 failed 业务结果，`main()` 仍返回 4，让 CI 不需要
解析全文就能知道任务失败。

### 13.10 本节记忆模型

```text
pyproject.toml 声明命令映射
→ pip install -e . 生成 luxar.exe
→ Shell 通过 PATH 找到启动器
→ 启动器导入 luxar.cli.main
→ main(None) 让 argparse 读取 sys.argv[1:]
→ main 返回整数作为进程退出码
```

最核心的一句话：

> `luxar` 是打包系统生成的操作系统命令入口；`main()` 才是我们编写的 Python
> 展示边界；LangGraph 要等 `main()` 完成参数处理和对象装配后才会真正执行。

---

## 十四、逐行教学二：终端参数怎样进入 LangGraph

这一节追踪下面这条数据链：

```text
终端字符串
→ argparse 解析并检查
→ args（Namespace 参数对象）
→ Bootstrap 装配 context（工具箱）
→ initial_state（任务状态）
→ Runner 启动 LangGraph
```

先区分三个容易混淆的对象：

```text
args           用户怎样要求本次程序运行
context        本次运行可以调用哪些真实能力
initial_state  Agent 当前知道哪些任务数据
```

### 14.1 `build_parser()` 是命令格式说明书

```python
parser = argparse.ArgumentParser(
    prog="luxar",
    description="运行 LUXAR ESP-IDF Agent 工作流",
)
```

`ArgumentParser` 创建一个参数解析器。这里没有解析任何实际命令，只是在定义：

```text
程序在帮助中叫什么：luxar
程序是做什么的：运行 LUXAR ESP-IDF Agent 工作流
```

接着定义子命令：

```python
subcommands = parser.add_subparsers(dest="command", required=True)
run_parser = subcommands.add_parser("run", help="运行一个固件任务")
```

这表示不能只输入 `luxar`，还必须选择一个动作。当前只有 `run`：

```powershell
luxar run ...
```

`dest="command"` 表示解析后将子命令保存在 `args.command` 中；这里它的值是
`"run"`。`required=True` 表示不写子命令就是参数错误。

为什么保留子命令结构？因为将来可以自然增加：

```text
luxar run
luxar inspect
luxar doctor
```

而不必创造 `luxar-run`、`luxar-inspect` 等互不相关的入口。

### 14.2 每个 `add_argument()` 在规定什么

项目路径：

```python
run_parser.add_argument("--project", type=Path, required=True)
```

含义是：

```text
--project       参数名称
type=Path       把收到的字符串转换成 pathlib.Path
required=True   用户必须提供
```

因此终端里的：

```text
"C:\projects\blink"
```

被转换为近似：

```python
Path("C:/projects/blink")
```

`type=Path` 只负责类型转换，不负责保证目录存在，所以 `main()` 后面仍要检查：

```python
if not project.exists() or not project.is_dir():
```

任务文字：

```python
run_parser.add_argument("--task")
```

没有指定 `type` 时，值默认是字符串；没有提供时则是 `None`。普通模式允许
`None`，因为程序会稍后调用 `input()` 询问用户。

最大构建次数：

```python
run_parser.add_argument(
    "--max-attempts",
    type=_positive_integer,
    default=3,
)
```

`argparse` 会把终端字符串交给 `_positive_integer()`：

```python
def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed
```

所以这里发生的是：

```text
终端 "5"  → int("5") → Python 整数 5
终端 "0"  → 主动抛出参数错误
终端 "abc" → int 转换失败，再翻译成参数错误
未提供     → 使用整数 3
```

两个开关参数：

```python
run_parser.add_argument(
    "--allow-dependency-downloads",
    action="store_true",
)
run_parser.add_argument("--json", action="store_true")
```

`store_true` 表示用户写了开关就是 `True`，没写就是 `False`：

```text
没有 --json  → args.json == False
带有 --json  → args.json == True
```

这和 `--task` 不同：开关后面不需要再跟 `true`。

### 14.3 `parse_args()` 返回的 `args` 是什么

```python
args = build_parser().parse_args(argv)
```

它返回 `argparse.Namespace`，可以把它理解为“参数收纳盒”。例如：

```python
main([
    "run",
    "--project", "C:/projects/blink",
    "--task", "修复 GPIO",
    "--max-attempts", "5",
    "--allow-dependency-downloads",
])
```

解析结果近似为：

```python
Namespace(
    command="run",
    project=Path("C:/projects/blink"),
    task="修复 GPIO",
    max_attempts=5,
    allow_dependency_downloads=True,
    json=False,
)
```

所以后面可以使用属性访问：

```python
args.project
args.task
args.max_attempts
```

它不是字典，因此这里不是 `args["project"]`。`Namespace` 也不是 LangGraph
对象，只属于 Python 标准库 `argparse`。

### 14.4 为什么解析成功以后还要检查

`argparse` 只能验证命令结构和声明过的类型。例如它能发现：

```text
漏写 --project
--max-attempts 不是正整数
传入了未知参数
```

但下面这些是 LUXAR 自己的运行规则：

```python
if not project.exists() or not project.is_dir():
    return 2

if args.json and args.task is None:
    return 2
```

第一条需要访问文件系统；第二条是我们的产品设计——JSON 模式用于自动化，不能
突然停下来等待人工输入。因此这些检查属于 `main()` 的展示边界，而不是
`argparse` 的通用职责。

### 14.5 任务文字为什么要 `strip()`

```python
task = args.task if args.task is not None else input("请输入固件需求：")
task = task.strip()
if not task:
    return 2
```

条件表达式从左到右读：

```text
如果用户提供了 --task → 使用 args.task
否则                    → 调用 input() 交互询问
```

`strip()` 去掉字符串首尾空白：

```text
"  闪烁 GPIO 2  " → "闪烁 GPIO 2"
"      "           → ""
```

第二个例子最终是假值，因此会被判定为空需求。这样不会把一串空格交给 LLM。

### 14.6 `args` 怎样分流成 `context` 和 `initial_state`

完成输入检查后，并不是把整个 `args` 都扔给 LangGraph，而是分成两类。

第一类是运行能力配置，交给 Bootstrap：

```python
context = build_deepseek_runtime_context(
    project_path=project,
    allow_dependency_downloads=args.allow_dependency_downloads,
)
```

Bootstrap（组合根）会创建并连接：

```text
DeepSeekJsonClient
DeepSeekRequirementParser
DeepSeekPlanner
DeepSeekRepairPlanner
EspIdfCliAdapter
LocalWorkspaceAdapter
```

最后把这些对象装入 `RuntimeContext`。它相当于本次 Agent 运行使用的“工具箱”。

这里还有一条重要安全链：

```text
用户显式写 --allow-dependency-downloads
→ args.allow_dependency_downloads == True
→ Bootstrap 传给 EspIdfCliAdapter
→ Adapter 才允许解析时下载依赖
```

没有这个开关时默认是 `False`。LLM 自己不能修改这项授权。

第二类是任务数据，组成初始 State：

```python
initial_state = WorkflowState(
    task_text=task,
    attempts=0,
    max_attempts=args.max_attempts,
    trace=[],
)
```

这里的数据含义是：

```text
task_text    要解决的自然语言任务
attempts     当前还没有构建，所以是 0
max_attempts 本次最多允许构建几次
trace        还没有执行任何节点，所以是空列表
```

注意 `context` 和 `state` 的区别：

```text
context 放“会做事的对象”   例如 parser、planner、workspace、espidf
state   放“流动的任务数据” 例如 requirement、plan、evidence、attempts
```

LangGraph 节点读取 State，并通过 Runtime 中的 Context 调用工具。工具不需要被塞进
State，也不会随着每一步快照反复复制。

### 14.7 Runner 才是进入 LangGraph 的应用入口

```python
result = run_workflow(
    initial_state=initial_state,
    context=context,
    progress_reporter=None if args.json else _report_progress,
)
```

这里传入三样东西：

```text
initial_state      Agent 从什么数据开始
context            Agent 能调用什么能力
progress_reporter  中途怎样向人报告进度
```

普通模式传 `_report_progress`，所以终端能看到阶段提示；JSON 模式传 `None`，防止
进度文字污染供程序读取的 JSON。

Runner 内部才执行：

```python
build_graph().stream(
    initial_state,
    context=context,
    stream_mode="values",
)
```

因此职责链是：

```text
CLI        处理不可信输入和展示
Bootstrap  创建并连接真实对象
Runner     统一运行、进度和异常边界
Graph      调度节点与状态流转
Node       完成一个业务步骤
Port       规定节点需要的能力合同
Adapter    连接 DeepSeek、文件系统、idf.py 等外部世界
```

CLI 没有直接调用 DeepSeek，也没有直接调用 `idf.py`，更没有自己决定下一个节点。

### 14.8 现有测试怎样证明这条链

测试没有真的调用 DeepSeek，而是替换两个边界函数：

```python
monkeypatch.setattr(cli, "build_deepseek_runtime_context", fake_bootstrap)
monkeypatch.setattr(cli, "run_workflow", fake_runner)
```

然后调用：

```python
cli.main([
    "run",
    "--project", str(tmp_path),
    "--max-attempts", "5",
])
```

Fake Bootstrap 记录自己收到的参数，Fake Runner 记录自己收到的 State 与 Context。
断言最终确认：

```python
calls["bootstrap"] == {
    "project_path": tmp_path,
    "allow_dependency_downloads": False,
}

runner_call["initial_state"] == {
    "task_text": "闪烁 GPIO 2",
    "attempts": 0,
    "max_attempts": 5,
    "trace": [],
}
```

这类测试的价值不是证明 DeepSeek 可用，而是精确证明 CLI 没有接错线：参数转换、
默认授权、任务清理和 State 初始化都符合设计。

### 14.9 本节记忆模型

```text
args           = 用户这一次怎样启动程序
context        = Agent 这一次拥有哪些工具
initial_state  = Agent 这一次从哪些数据开始
result         = LangGraph 最终返回的 State
```

最核心的一句话：

> `argparse` 只把终端文本整理成可信的 Python 参数；CLI 再把这些参数分流为
> RuntimeContext 和 WorkflowState，最后由 Runner 正式启动 LangGraph。
