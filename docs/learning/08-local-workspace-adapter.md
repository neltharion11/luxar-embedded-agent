# 08：LocalWorkspaceAdapter 与安全文件副作用

这一章复习 LUXAR 第一个真实工程 Adapter。它把模型生成的
`RepairPlan` 转换成受限制的本地文件修改，同时防止模型任意读取、创建
或覆盖项目外文件。

## 一、它在 Agent 中的位置

```text
build_project
→ BuildEvidence(success=False, error_category="source")
→ LangGraph 路由到 repair_project
→ LocalWorkspaceAdapter.read_project_files(project_path)
→ list[ProjectFile]
→ DeepSeekRepairPlanner
→ RepairPlan
→ LocalWorkspaceAdapter.apply_repair(project_path, repair)
→ 再次 build_project
```

LangGraph 负责何时进入修复节点；DeepSeek 负责提出结构化修复；
`LocalWorkspaceAdapter` 才是唯一能接触真实源码文件的对象。

## 二、英文术语对照

| 英文 | 中文 | 在当前实现中的意思 |
|---|---|---|
| Workspace | 工作区 | 一个受限制的 ESP-IDF 项目根目录 |
| Filesystem Adapter | 文件系统适配器 | 用真实本地文件实现 `WorkspacePort` |
| Allowlist | 允许列表 | 只有明确列出的源码后缀和配置名能被处理 |
| Containment | 路径包含关系 | 解析后的目标必须仍位于项目根目录内 |
| Symlink | 符号链接 | 可把一个路径重定向到另一个文件或目录 |
| Junction | 目录联接 | Windows 的目录重定向机制 |
| Byte Budget | 字节预算 | 单文件和一次调用允许处理的最大字节数 |
| Staging | 暂存 | 先把新内容写入同目录临时文件 |
| Commit | 提交替换 | 用 `os.replace` 把暂存文件换成正式文件 |
| Rollback | 回滚 | 后续提交失败时恢复已经改过的文件 |
| Atomicity | 原子性 | 操作要么全部成功，要么全部不发生的性质 |
| Sanitized Error | 脱敏错误 | 不包含绝对路径、系统异常和源码内容的错误 |
| Preflight | 前置检查 | 在产生副作用前完成全部验证 |

当前多文件方案可以处理普通 Python/I/O 失败并执行逻辑回滚，但不宣称在
断电、系统崩溃或进程被强制终止时具有跨文件原子性。真正的崩溃恢复需要
持久化事务日志，不属于当前切片。

## 三、两层路径安全

第一层在 Domain：

```text
FileReplacement.path
→ 拒绝空路径
→ 拒绝绝对路径和 Windows 盘符
→ 拒绝 ..
→ 统一成 POSIX 相对路径
```

第二层在真实 Adapter：

```text
相对路径
→ 拼接到严格解析后的项目根目录
→ 检查每个路径组件不是 symlink/junction
→ Path.resolve(strict=True)
→ resolved_target.relative_to(resolved_root)
→ I/O 前再次检查
```

`relative_to(root)` 是结构化路径判断。不能使用
`str(target).startswith(str(root))`，因为 `C:\project-evil` 在字符串上也
以 `C:\project` 开头，却不是其子目录。

Prompt 中的“不要返回绝对路径”只是第一道提醒。Domain 和 Adapter 的强制
验证才是不能被模型意愿绕过的安全边界。

## 四、允许和禁止的文件

允许的后缀包括 C/C++、汇编、CMake、链接脚本和分区 CSV；允许的精确文件
名包括 `CMakeLists.txt`、`Kconfig`、`sdkconfig.defaults` 和
`idf_component.yml`。

下列内容不会交给模型修改：

- `build`、`build_*`：构建产物；
- `managed_components`：组件管理器维护的依赖源码；
- `dependencies.lock`：工具维护的依赖锁；
- `sdkconfig`：生成配置且可能很大；
- `.git`、编辑器目录和隐藏目录；
- 二进制、含 NUL 的内容和非 UTF-8 文本。

`apply_repair()` 还要求目标必须已经存在且是普通文件，因此模型不能利用
修复功能创建新文件或删除文件。

## 五、为什么按字节限制

```python
replacement_bytes = replacement.content.encode("utf-8")
```

Python 的 `len("中")` 是 1 个字符，但 UTF-8 编码后是 3 个字节。磁盘、
网络上下文和内存真正消耗的是字节，所以默认限制是：

- 单文件：256 KiB；
- 一次读取或修复总量：1 MiB。

读取时先用 `stat().st_size` 快速拒绝大文件，读取后再用 `len(data)` 复查
实际字节数，避免文件在两步之间变化。

## 六、validate-stage-commit-rollback

### Validate：全部验证

`_prepare_replacements()` 会验证全部目标、保存原始字节并编码新内容。任何
目标失败时，没有临时文件，也没有正式文件被修改。

### Stage：全部暂存

`NamedTemporaryFile(delete=False, dir=target.parent)` 在目标旁边创建
`.luxar-*.tmp`。使用同一目录能让后续 `os.replace` 保持在同一文件系统内。

### Commit：依次替换

全部暂存成功后才依次调用：

```python
os.replace(item.staged_path, item.target)
```

它执行完整文件替换，避免直接写正式文件时留下半截内容。

### Rollback：逆序恢复

`committed` 只保存已经替换成功的目标。后续提交失败后：

```python
for item in reversed(committed):
    ...
```

使用保存的 `original_bytes` 逆序恢复。如果恢复本身失败，Adapter 返回
`WorkspaceError(category="rollback_failed")`，要求人工检查，而不是谎报
普通写入失败。

## 七、这次新增的 Python 语法

### `frozenset`

不可修改的集合，适合保存固定 allowlist。集合成员判断比在普通列表中逐项
搜索更直接。

### `PurePosixPath`

只处理路径字符串，不访问磁盘。Domain 已把分隔符规范为 `/`，因此它适合
拆分项目相对路径；真实磁盘验证仍使用 `Path`。

### 嵌套函数

`_discover_allowed_files()` 内的 `visit()` 可以直接使用外层的 `root` 和
`discovered_files`，用于递归扫描，同时把这个辅助函数限制在发现逻辑内部。

### 内部 dataclass

`_PreparedReplacement` 把同一次替换需要共同移动的数据放在一个对象里：
相对路径、目标、原字节、新字节和临时路径。名称以下划线开头，表示它不是
对外业务 API。

### `try/except/finally`

- `except` 把底层异常转换为稳定 `WorkspaceError`；
- `raise` 在回滚成功后重新抛出最初错误；
- `raise rollback_error from error` 表示“回滚失败由原提交失败触发”；
- `finally` 在成功和失败时都清理剩余临时文件。

### 联合异常捕获

Runner 使用：

```python
except (CapabilityError, WorkspaceError) as error:
```

括号中是可捕获的异常类型元组。`isinstance()` 再判断应使用模型错误转换器
还是工作区错误转换器。整个 Graph 仍只有一个应用层异常边界。

## 八、错误如何进入 LangGraph State

```text
LocalWorkspaceAdapter
→ WorkspaceError(category, safe message, retryable)
→ 异常穿过 repair_project 节点
→ run_workflow 的统一 except
→ workspace_error_to_workflow_error
→ WorkflowError(stage="repair", category="workspace")
→ failed(latest_state)
→ 返回 status="failed"
```

Runner 使用应用自己维护的固定消息表，不复制 `WorkspaceError.message`。因此
即使底层错误链带有绝对路径或 OS 文本，也不会进入 State、日志响应或未来
checkpoint。

`graph.stream(stream_mode="values")` 让 Runner 保存每个已完成节点后的完整
State，所以工作区失败仍保留 Requirement、Plan、BuildEvidence、诊断和尝试
次数。

## 九、测试真正证明了什么

测试只在 pytest 的 `tmp_path` 中执行真实 I/O，覆盖：

- allowlist 与排除目录；
- 路径排序和 POSIX 相对路径；
- UTF-8、NUL 和字节预算；
- 不存在目标、目录目标和工具管理目录；
- Windows Junction 拦截；
- 单文件和多文件成功替换；
- 任一预验证失败时零正式副作用；
- 第三个提交失败时逆序恢复前两个文件；
- 回滚失败时返回脱敏的 `rollback_failed`；
- Runner 保留失败前的最新 State；
- 七节点 LangGraph 拓扑不变。

普通 symlink 用例在当前 Windows 环境缺少创建权限时会 skip；Junction 用例已
实际运行。默认测试仍不会修改真实工程、调用 DeepSeek 或执行 `idf.py`。

## 十、下一切片

下一步实现 `EspIdfCliAdapter`：

```text
验证 ESP-IDF 环境和项目结构
→ idf.py reconfigure
→ 检查并解析依赖
→ 默认禁止下载缺失依赖
→ 只有 allow_dependency_downloads=True 才允许下载
→ idf.py build
→ stdout/stderr 转换成 BuildEvidence + BuildDiagnostic
```

Workspace 负责源码边界，ESP-IDF Adapter 负责工具环境、依赖和构建证据；
两个职责不能混在一个类里。
