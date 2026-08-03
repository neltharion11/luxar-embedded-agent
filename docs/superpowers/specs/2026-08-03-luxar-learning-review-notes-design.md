# LUXAR Agent 学习复习笔记设计

**日期：** 2026-08-03

**状态：** 教学结构已确认，等待书面规格复核

## 目标

为零基础学习者建立一份可反复阅读的中文总复习笔记，重点巩固英文技术名词、Agent/LangGraph 开发所需 Python 语法、LUXAR 整体架构和真实调用链。

## 文档结构

新建 `docs/learning/00-LUXAR-Agent-复习总览.md`，作为复习的唯一入口。现有 `01` 至 `07` 专题笔记保留，不重复改写；总览在相应位置链接到专题笔记，供需要时深入阅读。

总览固定包含：

1. 项目当前完成度和建议复习顺序；
2. 英文名词、中文含义、通俗解释、LUXAR 文件位置四列对照表；
3. Python 基础语法与项目中的真实用途；
4. Domain、Port、Adapter、Application、LangGraph、Bootstrap、Runner 分层架构；
5. 正常、澄清、构建修复、能力异常四条纵向链路；
6. 单元、契约、拓扑、集成、Smoke Test 的区别；
7. Agent 工程中的安全、不变量、错误边界和常见误解；
8. 一页式复习清单。

## 写作规则

- 先写中文含义，再解释英文名词，不假设读者已有工程背景。
- 每个抽象概念至少指向一个当前仓库中的真实文件或函数。
- 解释“它是什么、它负责什么、它不负责什么”。
- Python 语法重点覆盖 `Literal`、`BaseModel`、`Protocol`、普通类、`TypedDict`、`dataclass`、泛型、类型标注、默认值、关键字参数、解包、生成器、异常、`cast` 和 Pydantic 常用方法。
- 明确区分类型标注、运行时验证和真正的函数执行。
- 使用一个小型 Mermaid 架构图和必要的文本调用链，不堆砌装饰性图表。
- 不包含真实 API 密钥、未经验证的未来实现或已过时测试数字。

## 辅助修改

- 在 `README.md` 的学习区域增加总览入口。
- 在 `docs/learning/PROGRESS.md` 记录复习总览已经生成。
- 不修改任何 Python 业务代码或测试。

## 验收标准

1. 初学者能根据术语表说清 Client、Adapter、Port、Domain、State、Context、Node、Route、Graph、Bootstrap 和 Runner 的关系。
2. 初学者能沿文件路径追踪自然语言任务如何变成 Domain 对象并进入 State。
3. 初学者能解释项目中主要 Python 类型语法为什么存在。
4. 笔记与当前 `130 passed, 1 skipped` 检查点一致。
5. 所有本地文件链接和 Markdown 格式通过检查。
