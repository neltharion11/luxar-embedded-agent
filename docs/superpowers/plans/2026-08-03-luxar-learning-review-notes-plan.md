# LUXAR Agent 学习复习笔记实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not delegate documentation interpretation. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 生成一份面向初学者的中文 LUXAR Agent 总复习笔记，统一术语、Python 语法、分层架构、纵向链路、测试和安全知识。

**架构：** `00-LUXAR-Agent-复习总览.md` 作为唯一复习入口，现有 `01` 至 `07` 作为深入章节。总览中的每个抽象概念都映射到当前仓库的真实文件或函数，不改写业务代码。

**技术栈：** Markdown、Mermaid、Python 3.12、LangGraph、Pydantic、pytest。

## 全局约束

- 仓库：`C:\tmp\luxar-langgraph`。
- Codex 直接生成全部 Markdown，不要求学习者复制、排版或整理。
- 只使用当前源码、测试和学习笔记中已经实现并验证的事实。
- 当前检查点写为 `130 passed, 1 skipped`，并说明 Smoke Test 未联网。
- 不包含真实 API 密钥、真实用户路径示例或未实现能力的完成声明。
- 不修改 Python 业务代码或测试。

---

### Task 1：生成复习总览

**文件：**

- Codex 创建：`docs/learning/00-LUXAR-Agent-复习总览.md`

- [ ] 写明当前完成度、复习目标和推荐阅读顺序。
- [ ] 生成“英文名词、中文名称、通俗解释、LUXAR 位置”对照表，至少覆盖 Agent、Workflow、State Machine、Domain Model、Port、Adapter、Client、SDK、State、Runtime Context、Node、Route、Graph、Bootstrap、Runner、Dependency Injection、Fake、Evidence、Structured Output、JSON Schema、Validation、Error Boundary、Smoke Test。
- [ ] 解释普通类、`BaseModel`、`Protocol`、`TypedDict`、`dataclass` 的共同点和区别。
- [ ] 解释 `__future__.annotations`、`Literal`、类型标注与默认值、`self`、`__init__`、泛型、关键字参数、`| None`、列表/字典解包、列表推导、生成器、`try/except`、`raise ... from`、`cast`、Pydantic 常用方法。
- [ ] 用一个 Mermaid 图表示分层依赖，用文本分别描述正常、澄清、修复、能力异常四条纵向链路。
- [ ] 解释 pytest 如何真正执行代码，以及单元、契约、拓扑、集成、Smoke Test 的区别。
- [ ] 总结 Agent 工程中 Prompt 与 Domain 双重约束、路径安全、工具证据、有限重试、依赖隔离、错误脱敏等原则。
- [ ] 生成一页式复习清单和专题笔记链接。

---

### Task 2：增加入口并验证文档

**文件：**

- Codex 修改：`README.md`
- Codex 修改：`docs/learning/PROGRESS.md`
- Codex 修改：`docs/superpowers/specs/2026-08-03-luxar-learning-review-notes-design.md`
- Codex 修改：本计划

- [ ] 在 README 增加总复习入口和专题笔记说明。
- [ ] 在进度记录中写明总览覆盖范围和当前测试检查点。
- [ ] 将规格状态更新为已实现，并同步本计划复选框。
- [ ] 检查总览引用的所有本地文件存在。
- [ ] 搜索占位符、密钥样式和过时测试数字，运行 `git diff --check`。
- [ ] 提交为 `docs: consolidate LUXAR Agent learning notes`。

## 验收门槛

1. 总览可独立阅读，不依赖聊天上下文。
2. 术语解释包含职责边界和真实文件位置。
3. Python 语法解释能回答“来自哪里、运行时做什么、为什么这样写”。
4. 架构图和四条纵向链路与当前代码一致。
5. README 能直接引导到总览，原有专题笔记仍保留。
