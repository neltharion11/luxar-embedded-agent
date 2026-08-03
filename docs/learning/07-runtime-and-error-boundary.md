# 07：组合根、Runner 与统一错误边界

## 两个正式入口分别负责什么

```text
build_deepseek_runtime_context(...) 负责选择并连接具体能力
run_workflow(...)                   负责执行 Graph 并返回最终 State
```

组合根创建共享 DeepSeek Client，再创建需求解析、计划生成和修复规划三个 Adapter。它同时接收 ESP-IDF 与 Workspace Port 的具体实现，最后把这些对象装进 `RuntimeContext`。

Runner 不创建业务能力。它接收已经装配好的 `RuntimeContext`，执行原来的七节点 Graph，并保存每个成功步骤之后的完整 State。

## 一次正常调用

```text
自然语言 task_text
  → run_workflow
  → LangGraph analyze_requirement 节点
  → RuntimeContext.requirement_parser Port
  → DeepSeekRequirementParser Adapter
  → DeepSeekJsonClient
  → OpenAI 兼容 SDK
  → DeepSeek API
  → JSON 字典
  → FirmwareRequirement.model_validate
  → Requirement 写回 State
  → 后续计划、构建、修复或终态节点
```

Client 只理解网络请求和 JSON；Adapter 理解某一种业务任务；Domain Model 负责最终验证；LangGraph 负责状态推进和路线。

## 为什么异常只捕获一次

三个模型节点只描述正常业务逻辑。如果每个节点分别捕获异常，就会复制错误映射、脱敏文本和失败 State 更新。

`run_workflow()` 在 Graph 外围保留最后一次成功的 State，并包含唯一的 `except CapabilityError`。因此需求解析失败时仍保留原始任务，计划失败时保留 Requirement，修复失败时保留 Requirement、Plan、BuildEvidence 和诊断。

## 两层错误模型

```text
CapabilityError
外部能力层事实：认证、超时、限流、服务异常、JSON/Schema 异常

WorkflowError
工作流层事实：在哪个阶段失败、对用户显示什么、能否重试
```

Runner 不复制 `CapabilityError.message`，而是根据类别选择应用拥有的固定文字。这是第二道脱敏保障。

## `stream()` 在这里的意义

`graph.invoke()` 只在全部完成后返回最终 State；中途抛异常时，调用方拿不到普通返回值。

`graph.stream(stream_mode="values")` 会在成功步骤之后产生完整 State 快照。Runner 每次用新快照替换 `latest_state`，所以后面的模型调用失败时，前面已经验证过的数据仍然可用于诊断。

## 当前边界

这个 Runner 只统一处理模型 Adapter 抛出的 `CapabilityError`。真实文件系统和 ESP-IDF CLI 的失败策略将在它们各自的 Adapter 切片中设计：构建失败优先成为 `BuildEvidence`，而不是被伪装成模型错误。
