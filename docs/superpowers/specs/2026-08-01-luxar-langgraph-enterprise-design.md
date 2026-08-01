# 新版 LUXAR LangGraph 企业架构设计

**日期：** 2026-08-01

**状态：** 待学习者最终复核

## 1. 目标与边界

在 `C:\tmp\luxar-langgraph` 建立一个完全独立、可测试、可渐进迁移的新版 LUXAR。旧仓库 `C:\Users\Gugugu\Documents\Codex\LUXAR` 只作为既有行为和接口证据，不被新项目导入或修改；`C:\tmp\luxar-from-zero` 保留为 Lessons 0–2 的学习记录。

第一阶段打通以下最小纵向链路：

```text
自然语言任务
→ 需求解析
→ 结构化固件需求
→ 计划生成
→ 结构化执行计划
→ 假 ESP-IDF 构建
→ 结构化构建证据
→ 完成、有限重试或失败
```

第一阶段不接入真实 DeepSeek API、真实 `idf.py`、人工审批、持久化、烧录、串口监控、Web UI、RAG 或多 Agent。接口稳定后按独立纵向切片逐项加入。

## 2. 架构原则

新版采用最小化的企业分层：

```text
Domain      定义业务数据和不变量
Application 使用 LangGraph 编排业务流程
Ports       声明应用需要的外部能力
Adapters    实现 DeepSeek、ESP-IDF 等外部能力
```

依赖规则：

- Domain 不导入 LangGraph、模型客户端或 `subprocess`。
- Ports 可以引用 Domain 类型，但不包含供应商实现。
- Application 依赖 Domain 和 Ports，不直接导入具体 Adapter。
- Adapters 实现 Ports，并将外部响应转换成 Domain 对象。
- LangGraph State 保存动态业务数据；Runtime Context 注入不可持久化的 Parser、Planner 和 ESP-IDF 实例。
- 所有外部结果必须经过结构化验证；模型不得生成工具执行证据。
- 所有循环必须有计数器、最大次数、最后一次失败证据和明确终态。

## 3. 目录结构

```text
luxar-langgraph/
├── pyproject.toml
├── README.md
├── docs/
│   └── superpowers/
│       ├── specs/
│       └── plans/
├── src/luxar/
│   ├── domain/
│   │   ├── requirements.py
│   │   ├── plans.py
│   │   ├── evidence.py
│   │   └── errors.py
│   ├── application/
│   │   ├── state.py
│   │   ├── context.py
│   │   ├── nodes.py
│   │   ├── routing.py
│   │   └── graph.py
│   ├── ports/
│   │   ├── requirement_parser.py
│   │   ├── planner.py
│   │   └── espidf.py
│   ├── adapters/
│   │   ├── fake_requirement_parser.py
│   │   ├── fake_planner.py
│   │   ├── fake_espidf.py
│   │   ├── deepseek/
│   │   │   ├── client.py
│   │   │   ├── requirement_parser.py
│   │   │   └── planner.py
│   │   └── espidf_cli.py
│   └── bootstrap.py
└── tests/
    ├── domain/
    ├── application/
    ├── adapters/
    └── integration/
```

`domain/` 按需求、计划、证据和错误的不同变化原因拆分，避免形成旧式巨大 schema 文件。`application/` 将 State、运行依赖、节点、路由和 Graph 装配分开。`ports/` 保存稳定业务接口；`adapters/` 保存易变化的供应商和工具实现。`bootstrap.py` 是组合根，只负责根据配置选择并装配具体 Adapter。

## 4. 核心领域对象

第一阶段使用 Pydantic 2 定义：

- `FirmwareRequirement`：平台、目标芯片、功能和缺失字段。
- `PlanStep`：可验证动作的类型、说明和顺序。
- `ExecutionPlan`：有序步骤及计划状态。
- `BuildEvidence`：成功标志、参数化命令、退出码、stdout/stderr 摘要和错误类别。
- `WorkflowError`：错误类别、发生阶段、是否可重试、用户建议和证据引用。

Graph State 使用 `TypedDict` 引用这些领域对象，不把领域字段全部摊平，也不把客户端、Prompt、密钥或完整日志放进 State。

## 5. Ports 与 Adapters

业务 Ports：

```text
RequirementParser.parse(task_text) → FirmwareRequirement
Planner.create_plan(requirement) → ExecutionPlan
EspIdfPort.build(project_path) → BuildEvidence
```

第一阶段由 Fake Adapters 提供可控结果，验证成功、澄清、重试和失败路径。第二阶段增加 DeepSeek Adapters；第三阶段增加真实 ESP-IDF CLI Adapter。替换 Adapter 不得要求修改 Domain 或 Graph 核心拓扑。

## 6. DeepSeek 接入

技术组合固定为：

```text
LangGraph + DeepSeek API + 自定义 Ports
```

`DeepSeekRequirementParser` 和 `DeepSeekPlanner` 分别实现业务 Port。它们使用 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 和超时配置，通过 DeepSeek 的 OpenAI-compatible API 获取 JSON Output，再使用 Pydantic 转换和验证。

默认模型为 `deepseek-v4-flash`，复杂规划或源码修复可配置为 `deepseek-v4-pro`。协议客户端可以使用 DeepSeek 官方示例采用的兼容 Python 客户端；它只是传输实现细节，供应商和凭据始终是 DeepSeek。

Adapter 必须规范化空响应、无效 JSON、截断、Pydantic 验证失败、超时、限流和服务错误。模型只生成需求、计划和修复建议，不能声称构建成功或伪造 `BuildEvidence`。

## 7. LangGraph 的角色

LangGraph 是应用编排和耐久执行运行时，负责：

- 保存一次任务中持续变化的 State。
- 按拓扑执行需求分析、规划、构建和终态节点。
- 使用普通边、条件边和 `Command` 选择路线。
- 管理带显式上限的恢复循环。
- 后续通过 `interrupt()` 和 checkpointer 实现审批与恢复。
- 通过 Runtime Context 注入模型和 ESP-IDF Ports。
- 通过 streaming 暴露实时阶段变化。

LangGraph 不定义领域对象，不封装 DeepSeek API，不执行 `idf.py`，也不替代业务不变量。

## 8. 第一阶段数据流

1. 初始 State 只含用户 `task_text`。
2. `analyze_requirement` 从 Runtime Context 取得 `RequirementParser`，得到并保存 `FirmwareRequirement`。
3. 缺失必要字段时进入澄清终态；完整时进入 `create_plan`。
4. `create_plan` 调用 `Planner`，保存 `ExecutionPlan`。
5. `build_project` 调用 `EspIdfPort`，保存 `BuildEvidence`。
6. 路由根据结构化证据选择 `completed`、有限重试或 `failed`。
7. 最终 State 能回答需求、计划、执行命令、退出码、失败证据和终止原因。

## 9. 错误与安全

- 模型响应错误转换为结构化模型错误，不泄漏 API key 或完整敏感响应。
- 环境缺失和不可恢复工具错误不盲目重试。
- 真实 ESP-IDF Adapter 使用参数列表、固定工作目录、命令允许列表和超时。
- API key 只来自环境或 Settings，不进入 State、checkpoint、日志或 Git。
- 文件副作用在后续人工审批切片中置于批准节点之后。
- Graph recursion limit 是最后防线，业务循环仍必须自行证明有界。

## 10. 测试策略

- Domain 测试验证字段和业务不变量。
- Port 契约测试确保 Fake 与真实 Adapter 返回相同 Domain 类型。
- 节点测试验证节点只通过 Port 工作并返回最小 State 更新。
- 路由测试覆盖每个条件和 `Command` 目的节点。
- Graph 集成测试使用 Fake Adapters 验证完整路径和有限终止。
- DeepSeek 测试默认模拟客户端响应；真实 API 冒烟测试为显式可选项。
- 真实 ESP-IDF 测试为显式可选集成测试，不阻塞日常套件。

## 11. 技术栈

第一阶段核心依赖：LangGraph 1.2.x、Pydantic 2、Python 3.12。开发依赖：pytest 8、pytest-cov、mypy 和 Ruff。

DeepSeek 阶段增加 OpenAI-compatible Python 客户端与 `pydantic-settings`。ESP-IDF 阶段优先使用标准库 `pathlib`、`subprocess` 和 `shutil`。持久化阶段增加 LangGraph SQLite checkpointer，部署后再评估 PostgreSQL。CLI 和观测按需增加 Typer、Rich、structlog 和可选 LangSmith。

LangChain 第一阶段不引入；只有在多供应商消息、工具调用标准化确有收益时再评估。

## 12. 渐进迁移

新版先在独立仓库形成可运行纵向切片，再用测试对照旧 LUXAR 的 `ExecutionPlan`、`BuildResult` 和 `EspIdfAdapter` 行为。迁移采用：建立新接口、固定旧行为、编写兼容映射、迁移一个调用方、验证、继续迁移。禁止一次性覆盖旧实现。

## 13. 成功标准

- Fake 纵向链路从自然语言运行到结构化构建证据并到达明确终态。
- 替换 Fake 与 DeepSeek Parser/Planner 不修改 Graph 拓扑。
- 替换 Fake 与真实 ESP-IDF Adapter 不修改应用节点签名。
- 测试证明所有失败循环在配置上限终止。
- State、Runtime Context、Domain、Port、Adapter 和 compiled graph 的职责可独立解释和测试。
- 新仓库不导入旧 `luxar` 包，不写入旧仓库。
