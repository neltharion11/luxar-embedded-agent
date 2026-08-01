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

