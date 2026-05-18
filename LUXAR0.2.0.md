# LUXAR 0.2.0

LUXAR 0.2.0 是一次 **无过渡期、无双栈、无兼容壳** 的架构重写。目标不是继续修补当前的 prompt-first 系统，而是直接把 LUXAR 重建成一个 **Thin Harness, Fat Skills** 的嵌入式 agent 平台。

本文档是 LUXAR 0.2.0 的唯一正式设计规范。实现者应以本文为唯一准绳，不再围绕“是否保留旧体系”“是否继续堆 prompt”“是否再引入新的平行工具层”做二次决策。

---

## Why Rewrite

当前 LUXAR 的问题不是局部 bug，而是控制面本身设计错误，主要体现在：

1. **Prompt-first 架构过重**
   - 系统行为依赖厚 `prompt`、`gates`、人工禁令和 workflow 话术。
   - 约束主要存在于模型上下文，而不是 policy、测试、CI 和 runtime checks。

2. **工具与 workflow 冗余**
   - `generate_driver`、`fix_code`、`review_project`、`forge_project`、`project_context` 等都在承担“顶层任务心智模型”，导致职责重叠。
   - 用户和 agent 都需要理解很多“工具名”而不是少量通用原语。

3. **Skill 不是一等公民**
   - 当前 skill 更像 prompt 附件或成功后的总结文件，而不是 agent 在运行中主动加载、patch、晋升的程序性知识。

4. **错误的 harness 实体化**
   - 在当前 `codex/0.2.0` 分支上，已经出现了 `src/luxar/harness/` 和 `workspace/harnesses/`。
   - 这是一次设计误判：它把 harness 当成了与 skill 并列的 artifact 类型。
   - 这条路必须停止，并在 0.2.0 中直接回收。

5. **失败恢复与自我迭代不足**
   - 失败后常常是“重试、停机、人工修 prompt”。
   - 缺少明确的 `lesson -> skill patch -> 再验证` 闭环。

6. **硬件任务的验收标准错误**
   - “构建成功”经常被误当作“任务完成”。
   - 对嵌入式系统而言，运行时 evidence 比编译成功更重要。

因此，LUXAR 0.2.0 必须彻底放弃“厚 prompt + 碎片工具 + 流程硬编码”的旧范式，改成：

- 薄 harness
- 胖 skills
- 明确的 policy
- 少数强原语
- evidence 驱动验收
- lesson 驱动演化

---

## Design Principles

LUXAR 0.2.0 的设计原则固定如下：

1. **Thin Harness, Fat Skills**
   - 常驻上下文只保留最核心的边界与行为约束。
   - 程序性知识、领域工作流、恢复路径、最小验证路径都按需从 skill 加载。

2. **Harness 是系统，不是 artifact**
   - Harness 不是 `HARNESS.md`
   - 不是独立目录
   - 不是和 skill 并列的一类知识对象
   - 它是包围 agent 的行为约束系统

3. **Skill 是唯一的一等程序性 artifact**
   - Agent 只需要加载、执行、patch、晋升 skill
   - 不再要求 agent 判断“这是 skill 还是 harness”

4. **Lesson 先于 Skill**
   - 失败经验先进入 lesson
   - lesson 验证有效后再 patch 或 promote 到 skill
   - 一次性调试噪声不进入正式 skill

5. **Memory 只存稳定事实**
   - workflow、procedure、task progress 不进入 durable memory
   - memory 用于长期稳定偏好、工具链事实、硬件约定

6. **Declarative over Procedural**
   - Harness 负责定义目标、边界、验收和升级条件
   - Agent 自己探索执行路径
   - 不再通过 workflow 文件和 prompt 指令写死路径

7. **Mechanical Guardrails over Prompt Lectures**
   - 约束进入 policy、tests、CI、runtime checks
   - 不进入厚 prompt
   - 不做“道德说教式”提示词治理

8. **Evidence over Confidence**
   - 完成标准依赖可验证结果
   - 构建成功不是嵌入式任务完成
   - 必须有 runtime evidence 才能完成硬件相关任务

9. **少数强原语**
   - 工具要少而强
   - 不再让用户和 agent 记住一堆顶层 workflow 名字

10. **无过渡期**
   - 0.2.0 不保留双栈设计
   - 旧结构如仍保留少量逻辑，也只能作为 runtime 内部 worker 被吸收
   - 不能继续作为公开心智模型存在

---

## Harness Definition

### 一句话定义

> Harness 负责约束 agent 行为的边界与验收，不负责规定 agent 的具体执行路径。

### Harness 不是什么

Harness **不是**：

- `HARNESS.md`
- 一个独立 artifact 类型
- 一个“比 skill 更底层的知识对象”
- 一组固定 workflow 步骤
- 一份更长的系统 prompt

### Harness 是什么

Harness 是围绕 agent 的运行时行为约束系统，至少包含六层：

1. **Goal and Boundary Layer**
   - 定义目标、不可破坏的边界、验收要求、升级条件

2. **Context Governance Layer**
   - 管理上下文注入、压缩、重置、handoff
   - 控制信息分层披露，而不是一次性灌满 prompt

3. **Tool Primitive Layer**
   - 提供少量强原语
   - 规定权限边界与可执行动作

4. **Observability and Evidence Layer**
   - 规定哪些结果必须可观测、可留痕、可验证
   - 构建、烧录、串口、探测、运行时行为都属于 evidence

5. **Escalation Layer**
   - 明确哪些问题 agent 必须升级给人
   - 升级时必须附带上下文、方案、风险、推荐，不允许空问

6. **Self-Improvement Layer**
   - 管理 lesson 记录、skill patch、skill promotion、技术债清理

### Harness 与 Skill 的关系

- Harness 规定边界与验收
- Skill 提供程序性知识与操作方法
- Agent 在 harness 内部运行，并按需加载 skill

因此：

- Harness 是 **系统层**
- Skill 是 **artifact 层**

---

## Core Runtime Model

LUXAR 0.2.0 的唯一正式执行模型是统一 runtime 主循环：

```text
observe
→ classify
→ session_recall
→ load skills
→ derive plan
→ act
→ validate
→ record lesson / evidence
→ patch or promote skill
→ continue or escalate
```

### 各阶段定义

1. **observe**
   - 读取用户目标、当前工作区状态、现有项目事实

2. **classify**
   - 识别任务是 bring-up、integration、protocol、recovery、治理任务中的哪类

3. **session_recall**
   - 搜索历史会话与 lesson，获取相似项目和失败模式

4. **load skills**
   - 根据任务类型按需加载 relevant skills
   - skill 是唯一程序性 artifact

5. **derive plan**
   - 由 agent 根据目标、边界、skills 和环境自行推导路径

6. **act**
   - 调用 runtime 内部 workers 和 workspace primitives 执行动作

7. **validate**
   - 基于 evidence 判定当前目标是否满足

8. **record lesson / evidence**
   - 将失败、发现、验证结果沉淀到 lesson 和 evidence

9. **patch or promote skill**
   - 如果经验可复用，则 patch 相关 skill 或提交 promotion candidate

10. **continue or escalate**
   - 若未满足验收且仍可推进则继续
   - 若达到升级条件则提交给人类判断

### Runtime 的核心定位

- Runtime 是唯一正式 orchestrator
- 不再有平行主工作流系统
- 旧的 `forge`、`generate-driver`、`fix-code`、`review` 等都只能作为 runtime 内部 worker

---

## Repository Directory Index

LUXAR 0.2.0 的目标代码目录固定如下：

```text
src/luxar/
  agent/
    runtime.py
    loop.py
    planner.py
    policy.py
    context_builder.py
    recall.py
    escalation.py
    promotion.py
    explain.py

  skills/
    registry.py
    loader.py
    matcher.py
    manager.py
    provenance.py

  memory/
    memory_manager.py
    session_search.py
    lesson_store.py
    transcript_store.py
    recall.py

  tools/
    runtime_tool.py
    skills_tool.py
    memory_tool.py
    workspace_tool.py

  api/
    app.py
    schemas.py
    events.py

  cli/
    main.py

  policy/
    immutable_policy.md
    rules.py

  domains/
    embedded/
      capability_map.py
      task_classifier.py
      bringup_router.py
      integration_router.py
      recovery_router.py
```

### 各目录职责

- `agent/`
  - runtime 主循环、规划、策略、晋升、解释、升级逻辑

- `skills/`
  - skill 的扫描、加载、匹配、管理、来源追踪

- `memory/`
  - durable memory、session recall、lesson 存储、transcript 搜索

- `tools/`
  - 对 runtime 暴露的少数强原语

- `api/`
  - Web API 的正式入口、schema、事件模型

- `cli/`
  - CLI 的单一正式入口

- `policy/`
  - 不可变规则与可执行约束定义

- `domains/embedded/`
  - 嵌入式任务分类与路由辅助逻辑

### 当前分支上已经出现、但属于临时误建并待删除的结构

以下目录和文件已经存在于 `codex/0.2.0` 分支，但在 0.2.0 最终架构里被定义为 **误建**：

- `src/luxar/harness/`
- `workspace/harnesses/`

它们的责任会被回收并并入：

- `src/luxar/skills/`
- `src/luxar/tools/workspace_tool.py`
- `workspace/skills/`

---

## Workspace Directory Index

LUXAR 0.2.0 的目标工作区目录固定如下：

```text
workspace/
  projects/
  driver_library/
  firmware_library/
  toolchains/
  docs/

  skills/
    protocols/
    boards/
    bringup/
    recovery/
    workflows/

  lessons/
    draft/
    promoted/

  memory/
    MEMORY.md
    USER.md

  prompts/
    system.md
```

### 各目录职责

- `projects/`
  - 用户项目工作区

- `driver_library/`
  - 可复用驱动、知识库与文档解析产物

- `firmware_library/`
  - MCU / vendor firmware 资源

- `toolchains/`
  - 构建、烧录和调试工具链

- `docs/`
  - 参考资料、datasheet、接口文档

- `skills/`
  - 正式的 skill artifact 库

- `lessons/`
  - 尚未或已经晋升的经验对象

- `memory/`
  - durable memory 文件

- `prompts/system.md`
  - 唯一的薄系统提示词

### 当前工作区上存在、但 0.2.0 中必须删除的结构

- `workspace/harnesses/`
- `workspace/skill_library/`

其中：

- `workspace/harnesses/` 是误建目录，直接删除
- `workspace/skill_library/` 是 legacy 目录，直接废弃，其有价值内容人工吸收入 `workspace/skills/`

---

## Skill, Lesson, Memory, Recall

### Skill

Skill 是 LUXAR 0.2.0 的唯一一等程序性 artifact。

Skill 的四种模式：

- `knowledge`
- `workflow`
- `executable`
- `recovery`

#### Skill 目录规范

```text
workspace/skills/<category>/<name>/
  SKILL.md
  references/
  scripts/
  templates/
  assets/
```

#### Skill frontmatter 最小字段

- `name`
- `category`
- `mode`
- `promotion_level`
- `triggers`
- `verification`
- `related_lessons`
- `references`

#### 各模式含义

- `knowledge`
  - 原理、限制、常见坑、通用知识

- `workflow`
  - 一整套复合操作策略

- `executable`
  - 带可执行验证路径的 skill
  - 取代此前错误设计的 harness artifact

- `recovery`
  - 故障恢复策略

### Lesson

Lesson 是单次尝试中形成的经验候选，先于 skill。

Lesson 最小字段：

- `topic`
- `symptom`
- `hypothesis`
- `evidence`
- `resolution`
- `outcome`
- `promotable`

#### Lesson 原则

- 失败经验先进入 lesson
- lesson 可搜索、可晋升、可删除
- 一次性噪音不直接进入正式 skill

### Memory

Memory 只存稳定事实，例如：

- 用户偏好
- 项目惯例
- 板卡约定
- 工具链事实

#### Memory 严禁存储

- task progress
- 一次性结果
- 已完成日志
- procedure
- workflow
- 临时调试信息

### Session Recall

`session_search` 用于跨会话召回历史任务信息，提供：

- 相似问题
- 相似项目
- 相似修复路径

它不是：

- skill 替代品
- memory 替代品
- 常驻上下文替代品

### 数据流关系

```text
single attempt
→ lesson
→ validated pattern
→ skill patch / promotion

stable long-term fact
→ memory

historical execution trace
→ session_search / transcript recall
```

---

## Tooling and Public Interfaces

LUXAR 0.2.0 只保留 4 组顶层工具原语。

### 1. `runtime`

- `run`
- `explain`

职责：
- 启动统一 runtime 主循环
- 解释当前 runtime 决策模型

### 2. `skills`

- `list`
- `view`
- `manage`
- `promote`

职责：
- 管理所有程序性 artifact

### 3. `memory`

- `read`
- `write`
- `search`
- `lesson_record`
- `lesson_promote`

职责：
- 管理 durable memory、lesson 和记忆搜索

### 4. `workspace`

- `inspect`
- `build`
- `flash`
- `monitor`
- `probe`

职责：
- 提供对外部环境的少数强原语

### 正式公开 CLI

CLI 只保留：

- `luxar run`
- `luxar skills ...`
- `luxar memory ...`
- `luxar workspace ...`

### 正式公开 API

API 只保留：

- `/api/runtime/*`
- `/api/skills/*`
- `/api/memory/*`
- `/api/workspace/*`
- `/api/session-search`

### 正式事件模型

只保留：

- `phase_changed`
- `skill_loaded`
- `lesson_recorded`
- `promotion_candidate_created`
- `promotion_applied`
- `escalation_triggered`

不得再以旧工具名来组织 API 与前端心智模型。

### 不再存在的顶层工具心智模型

以下名字不再作为顶层工具保留：

- `generate_driver`
- `fix_code`
- `review_project`
- `forge_project`
- `project_context`

它们以后统一只是 **runtime workers**。

---

## Agent Workflows

### 统一工作流原则

- Harness 只定义边界、验收和升级条件
- Agent 自己探索路径
- Skill 提供按需加载的程序性知识
- Workspace primitives 提供真实执行能力
- Evidence 决定是否完成

### 1. 新项目或集成任务

```text
用户给目标与边界
→ classify 为 integration / bringup / protocol
→ recall 历史 session 与 lessons
→ load skills
→ derive integration plan
→ act with runtime workers
→ validate with build / flash / runtime evidence
→ complete or escalate
```

关键要求：

- 不直接依赖固定 workflow 文件
- 不先灌一长串 prompt 规则
- 不因“编译成功”而提前判定完成

### 2. Bring-up 任务

```text
识别为 bringup
→ load bringup / executable skills
→ 先做最小验证
→ 采集 evidence
→ 再决定是否进入更高层业务逻辑
```

关键要求：

- 不直接跳到完整业务功能集成
- 不因代码生成完成而跳过最小验证

### 3. Recovery / Debug 任务

```text
识别失败症状
→ recall 相似 failures
→ load recovery skills
→ 选择最小修复路径
→ 验证
→ 失败则 lesson 化
→ 达到升级条件则提交给人
```

关键要求：

- 不做盲目多轮重试
- 不允许“失败次数达到阈值就停”这种粗糙策略作为核心设计
- 升级给人类时必须附：
  - 当前理解
  - 可选方案
  - 风险比较
  - 推荐意见
  - 当前 evidence

### 4. 后台治理任务

```text
扫描陈旧 skill
→ 搜索长期失败 lesson
→ patch or prune skills
→ 清理技术债
→ 用小步演化保持系统健康
```

关键要求：

- 用 agent 治理 agent
- 不等待大规模腐化后再一次性重构

---

## Policy, Evaluation, and Escalation

### 不可变规则

写入 `policy/immutable_policy.md` 的至少包含：

- 不得伪造 build / flash / monitor / probe / hardware evidence
- 不得绕过 evidence gate 直接晋升 skill
- 不得把 task progress 写入 durable memory
- 不得创建新的 prompt-first workflow 模块
- 不得把 harness 重新物化为独立 artifact 类型

### Mechanical Guardrails

约束的首选落点是：

- repo structure checks
- linter
- architecture tests
- skill schema validation
- promotion prechecks
- runtime evidence checks
- escalation rules

而不是：

- 厚 prompt
- workflow 话术
- 对 agent 的抽象道德教育

### 验收原则

Agent 的完成不是“自认为完成”，而是“满足 evidence 验收”。

至少需要：

- 构建 evidence
- 烧录 evidence
- 运行时或硬件 evidence
- 审查/验证 evidence

硬件任务中：

- `build passed` 绝不等于 `task complete`

### 升级原则

必须升级给人的情形包括：

- 不可逆操作
- 业务取舍
- 权限判断
- 代码和环境中无法推导出的偏好选择
- 长期失败后需要人类裁决的问题

升级输出格式必须包含：

- 背景
- 当前理解
- 已尝试内容
- 可选方案
- 风险比较
- 推荐方案
- 当前 evidence

---

## Deletions and Replacements

0.2.0 必须大刀阔斧删除以下内容，并给出替代关系。

### 删除与替代

- `prompt-first gates`
  - 替代：`policy + tests + runtime checks`

- `harness artifact`
  - 替代：`executable skill`

- `workflow 文件系统`
  - 替代：`runtime 主循环`

- `core 级专用 orchestrator`
  - 替代：`runtime workers`

- `legacy skill_library`
  - 替代：`workspace/skills`

- `project_context`
  - 替代：`workspace inspect + recall + skills`

- `fix_code` 顶层工具
  - 替代：`runtime internal repair worker`

- `generate_driver` 顶层工具
  - 替代：`runtime internal generation worker`

- `forge_project` 顶层工具
  - 替代：`runtime run`

### 必删目录

- `src/luxar/harness/`
- `workspace/harnesses/`
- `workspace/skill_library/`
- `src/luxar/workflows/`

### 必删主控制面

以下模块不再作为主控制面存在：

- `src/luxar/prompts/`
- `src/luxar/core/project_planner.py`
- `src/luxar/core/driver_generator.py`
- `src/luxar/core/app_generator.py`
- `src/luxar/core/code_fixer.py`
- `src/luxar/tools/forge_project.py`
- `src/luxar/tools/generate_driver.py`
- `src/luxar/tools/fix_code.py`
- `src/luxar/tools/review_code.py`
- `src/luxar/tools/run_task.py` 的旧编排模式

如果仍有逻辑价值，只能拆散吸收进 runtime workers。

---

## Test and Acceptance Criteria

### Skill 验收

- skill 可按需加载
- `executable skill` 能驱动 runtime action
- `recovery skill` 能改变失败路径
- patch / promote 流程可追踪

### Lesson 验收

- 失败会记录 lesson
- lesson 可搜索
- lesson 可晋升 skill patch
- 一次性噪声不会污染正式 skill

### Memory / Recall 验收

- durable memory 不包含 task progress
- session_search 能改变执行策略
- recall 不会膨胀常驻上下文

### Runtime 验收

- `luxar run` 只依赖薄 prompt + skill 加载
- 重复失败不会变成盲重试
- 升级给人类时包含上下文、选项、风险、推荐

### Embedded 验收

- OLED / bus bring-up 不直接跳到业务逻辑
- “编译通过但硬件不亮” 不会误判成功
- 硬件类任务必须有 runtime evidence 才能完成

### Deletion 验收

- 仓库内不再存在独立 harness artifact 体系
- 不再存在 prompt-first 主工作流入口
- 新能力不能再通过旧架构新增

---

## Non-Goals

LUXAR 0.2.0 明确不做以下事情：

- 不保留旧 CLI/API 兼容
- 不保留独立 harness artifact
- 不追求多框架并存
- 不在 0.2.0 中继续扩展更多顶层工具族
- 不把全文写成 prompt 规范大全
- 不把 memory 当 skill 的替代品
- 不把 lesson 当长期正式知识库
- 不把工作流再物化成一堆新名字的工具

---

## 当前分支状态说明

`codex/0.2.0` 当前已经做过一批 vNext 试探性改造，其中：

### 已经方向正确的结构

- `src/luxar/agent/`
- `src/luxar/skills/`
- `src/luxar/memory/`
- `src/luxar/api/`
- `src/luxar/policy/`
- `workspace/skills/`
- `workspace/memory/`
- `workspace/prompts/system.md`

### 已被本文明确判定为临时误建、待删除的结构

- `src/luxar/harness/`
- `workspace/harnesses/`

### 已被本文明确判定为 legacy 主控制面、待删除或被吸收的结构

- `src/luxar/prompts/`
- `src/luxar/core/` 中以 prompt-first 为中心的生成/修复/规划路径
- `src/luxar/tools/` 中以任务名为心智模型的旧顶层入口

实现 0.2.0 时，必须以本文为准重新收口，而不是继续围绕误建结构扩展。
