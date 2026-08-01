# 领域模型：需求、计划与证据

## FirmwareRequirement

`FirmwareRequirement` 是新版 LUXAR 的第一个领域对象。它把自然语言或模型响应转换为经过验证的固件需求，且不依赖 LangGraph 或任何模型供应商。

当前字段表达平台、目标芯片、功能、可选 GPIO 和缺失字段。`is_complete` 从 `missing_fields` 推导，避免 Graph State 同时保存字段列表和可能与其矛盾的布尔值。

Pydantic 负责运行时数据验证；LangGraph State 后续只负责引用该对象并推进工作流。DeepSeek Adapter 必须先构造出合法的 `FirmwareRequirement`，节点才能将它写入 State。

## 已验证不变量

- 默认平台固定为 `espidf`。
- 无缺失字段时需求完整。
- 有缺失字段时需求不完整。
- 每个对象拥有独立的可变默认列表。
- 非 ESP-IDF 平台在领域边界被拒绝。

Verified on 2026-08-01: `4 passed`.

## ExecutionPlan

`PlanStep.kind` 是机器可读的受支持动作，`description` 是面向人的说明。`ExecutionPlan` 至少包含一个步骤。DeepSeek 可以提出计划，但输出必须先通过这些领域约束，才能进入 Graph State。

## BuildEvidence

`BuildEvidence` 保存参数化命令、退出码、输出摘要和错误类别。模型不能创建可信构建证据；只有执行工具的 Adapter 可以产生它。模型级后置验证器保证成功标志、退出码和错误类别互相一致。

## WorkflowError

`WorkflowError` 将 SDK 异常和工具故障转换为 LUXAR 自己的阶段、类别、可重试性和用户建议。Graph 因而依赖稳定业务语言，而不是供应商异常类型或日志字符串。

完整领域套件于 2026-08-01 验证：`15 passed`。
