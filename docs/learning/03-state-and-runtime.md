# LangGraph State 与 Runtime Context

`WorkflowState` 保存会随节点执行变化、值得观察和持久化的业务进展：需求、计划、证据、错误、计数器、状态和 trace。

`RuntimeContext` 保存本次调用使用但不应进入 checkpoint 的依赖：Parser、Planner、ESP-IDF 和项目路径。冻结 dataclass 防止节点在运行中替换这些依赖。

```text
State   = 任务现在进行到哪里
Context = 本次任务由谁提供外部能力
```

核心层不导入具体 Adapter；Graph 调用时才把 Fake 或生产实现注入 Context。

Verified on 2026-08-01: State/Context boundary test passes; full suite `20 passed`.

