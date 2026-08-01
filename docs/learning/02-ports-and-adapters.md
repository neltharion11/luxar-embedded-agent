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

