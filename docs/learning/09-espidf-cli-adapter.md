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
