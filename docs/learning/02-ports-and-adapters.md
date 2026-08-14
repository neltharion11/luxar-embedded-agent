# Ports 与 Adapters

## 应用拥有接口

新版 LUXAR 用三个业务 Port 表达外部能力：`RequirementParser`、`Planner` 和 `EspIdfPort`。Application 依赖这些稳定契约，DeepSeek 与 ESP-IDF CLI 作为 Adapter 实现契约。

`Protocol` 使用结构化类型：对象只要提供兼容的方法签名即可满足 Port，无需继承。Port 中禁止出现供应商 SDK、Prompt、密钥、subprocess、LangGraph State 或路由策略。

## 数据边界

```text
task_text → RequirementParser → FirmwareRequirement
FirmwareRequirement → Planner → ExecutionPlan
Path → EspIdfPort → BuildEvidence
```

这些返回值都是 Domain 对象，因此 Fake、DeepSeek 和真实 ESP-IDF 必须遵守相同业务边界。

Verified on 2026-08-01: Port imports succeed, no provider/tool leakage, and the 15-test suite remains green.

## Fake Adapters

三个 Fake 通过构造函数接收预设 Domain 对象并记录调用。`FakeEspIdf` 依次返回配置的 `BuildEvidence`，支持稳定验证失败后重试成功；证据耗尽时抛出测试配置错误，不静默编造工具事实。

Verified on 2026-08-01: 4 adapter contract tests and 19 total tests pass.
