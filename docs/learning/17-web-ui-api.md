# 17：原版 UI 怎样接入新版 LangGraph

## 一、这一层解决什么问题

新版 LUXAR 原来只有命令行入口：

```text
PowerShell → CLI → Bootstrap → Runner → LangGraph
```

现在增加 Web 入口：

```text
Browser → FastAPI/SSE → Bootstrap → Runner → LangGraph
```

两条链从 Bootstrap 开始完全相同。因此 Web 不是第二套 Agent，也没有复制七节点
Graph。CLI 和 Web 只是两个 Presentation Adapter（展示适配器）。

## 二、为什么不能让 Web 调用 CLI 子进程

错误结构是：

```text
Browser → FastAPI → subprocess(luxar run) → Runner
```

它会增加一层文本解析、进程管理、取消和错误码转换，而且 Web 很难直接获得安全的
进度对象。

当前结构直接调用 Python 应用函数：

```python
context = build_deepseek_runtime_context(...)
result = run_workflow(
    initial_state=initial_state,
    context=context,
    progress_reporter=report,
)
```

所以 CLI 与 Web 共享同一套真实业务规则。

## 三、为什么浏览器只能传项目名

浏览器发送：

```text
Demo
```

不能发送：

```text
C:\Users\...\project
..\outside
group/project
```

服务启动时由可信管理员明确指定：

```powershell
luxar-web --projects-root C:\projects
```

`WebProjectCatalog` 只把名称解析为：

```text
C:\projects\Demo
```

并检查它是根目录的直接子目录、不是符号链接/Junction，而且包含根
`CMakeLists.txt`。这叫 trust boundary（信任边界）：用户输入不能决定任意磁盘
位置。

## 四、HTTP 与 SSE 分别做什么

HTTP 普通接口适合一次请求得到一次结果：

```text
GET /api/health
GET /api/workspace/projects
GET /api/conversations/{project}
POST /api/conversations/{project}/reset
```

Agent 运行时间较长，需要持续返回进度，所以任务接口使用 SSE：

```text
POST /api/conversations/{project}
Content-Type: application/json
Accept: text/event-stream
```

服务端依次发送：

```text
event: progress
data: {"stage":"build", ...}

event: result
data: {"status":"completed", ...}

event: done
data: [DONE]
```

SSE 是 Server-Sent Events（服务器发送事件）。它是一条服务器持续向浏览器发送文本
事件的 HTTP 连接，适合当前这种“浏览器提交一次任务，服务器持续报告”的单向
进度流。

## 五、同步 Runner 怎样接到流式 Web

`run_workflow()` 是同步函数，而 FastAPI 还要继续服务其他请求。因此 Web Adapter
在后台线程运行 Runner，并使用有界队列连接两边：

```text
Runner thread
  progress_reporter(progress)
        ↓ queue.put
bounded Queue
        ↓ queue.get
SSE generator
        ↓
Browser
```

队列里只放 `WorkflowProgress` 的三个字段：

```text
stage
message
attempts
```

不会把完整 State、源码、项目绝对路径或 Context 当作进度发送。

## 六、为什么 CLI 和 Web 要共享结果白名单

结果序列化移动到：

```text
luxar.application.results.state_to_result
```

CLI JSON 和 Web SSE 的 `result` 都调用它，只允许：

```text
status
exit_code
attempts
requirement
plan
build_evidence
repair_plan
changed_files
error
trace
```

不能直接执行：

```python
json.dumps(state)
```

因为未来 State 可能增加任务原文、内部控制字段或其他不应离开应用边界的数据。
白名单的价值是“新增内部字段默认不会被公开”。

## 七、原版 UI 保留了什么

保留：

```text
布局和配色
侧边栏项目列表
聊天输入
Markdown 展示
进度状态
结果卡片
中英文外壳
```

暂时隐藏并覆盖成“尚未接入”：

```text
创建、导入、删除项目
附件
Driver Library
Skill Library
浏览器修改模型密钥
烧录、串口和硬件探测
真正的后台取消
```

这是为了避免界面看起来能做，但实际上偷偷调用已经不存在的旧后端。

## 八、为什么停止按钮暂时隐藏

浏览器的 `AbortController` 只能断开浏览器请求。ESP-IDF 构建和 LangGraph 工作流
可能仍在后台执行。

因此当前不能把“断开 SSE”称为“取消任务”。真正取消需要：

```text
运行 ID
应用层取消合同
节点/工具检查取消信号
安全终止 idf.py 子进程
一致的最终 State
```

在这些能力完成以前隐藏停止按钮，是准确表达系统能力，而不是功能倒退。

## 九、并发保护

当前 Web 层：

```text
同一项目同时只允许一个任务
整个服务最多运行固定数量任务
进度队列有容量上限
```

同一项目并发写源码会产生竞态条件，因此第二个请求返回 HTTP 409。服务达到总并发
上限时返回 HTTP 429。

## 十、完整纵向链路

```text
原版 UI
→ POST 项目名和任务
→ WebTaskRequest 严格验证
→ WebProjectCatalog 安全解析目录
→ Bootstrap 创建 DeepSeek/Workspace/ESP-IDF Adapters
→ Runner stream 七节点 LangGraph
→ WorkflowProgress 进入有界队列
→ FastAPI 编码 SSE progress
→ 最终 State 经过共享白名单
→ SSE result
→ UI 渲染状态、构建次数、错误建议和修改文件
```

最核心的一句话：

> UI 负责交互，FastAPI 负责网络边界，Runner 负责应用执行，LangGraph 负责编排，
> Adapter 负责外部能力；任何一层都不应越过下一层直接接管所有职责。
