# Luxar 实施拆解（基于 SPEC v1.2）

本文档将 [SPEC.md](C:\Users\Gugugu\Documents\Codex\2026-04-24-review-spec-md-kimi-code-codex\SPEC.md) 压缩为“可直接开工”的执行版任务清单，目标是让 Codex/Kimi Code 按顺序落地，而不是在实现过程中反复补架构。

---

## 1. 交付目标

第一阶段交付一个可运行的最小闭环：

```text
init project
→ check stm32 .ioc
→ generate App/ code
→ review generated code
→ build
→ flash
→ monitor
→ record logs / snapshot / git commit
```

第二阶段开始补驱动库与 Review Loop。

第三阶段补 PDF/RAG 与语义审查。

第四阶段补 Skill Evolution。

第五阶段补多平台适配（ESP-IDF / LinuxHost / FreeRTOS）。

---

## 2. 推荐目录树

```text
Luxar/
├─ pyproject.toml
├─ README.md
├─ SPEC.md
├─ IMPLEMENTATION_PLAN.md
├─ config/
│  └─ luxar.yaml
├─ prompts/
│  ├─ driver_generation.py
│  ├─ fix_code.py
│  ├─ semantic_review.py
│  └─ skill_evolution.py
├─ templates/
│  ├─ cmake/
│  │  └─ CMakeLists.txt.j2
│  ├─ app/
│  │  ├─ app_main.c.j2
│  │  ├─ app_main.h.j2
│  │  └─ app_config.h.j2
│  ├─ driver/
│  │  ├─ driver_h.j2
│  │  └─ driver_c.j2
│  └─ skill/
│     └─ protocol_skill.md.j2
├─ src/
│  └─ luxar/
│     ├─ __init__.py
│     ├─ cli.py
│     ├─ models/
│     │  ├─ __init__.py
│     │  └─ schemas.py
│     ├─ core/
│     │  ├─ __init__.py
│     │  ├─ config_manager.py
│     │  ├─ logger.py
│     │  ├─ project_manager.py
│     │  ├─ backup_manager.py
│     │  ├─ git_manager.py
│     │  ├─ lock_manager.py
│     │  ├─ review_engine.py
│     │  ├─ build_system.py
│     │  ├─ flash_system.py
│     │  ├─ uart_monitor.py
│     │  ├─ driver_library.py
│     │  ├─ pdf_parser.py
│     │  ├─ knowledge_base.py
│     │  ├─ skill_manager.py
│     │  └─ platform_adapter.py
│     ├─ platforms/
│     │  ├─ __init__.py
│     │  ├─ stm32_adapter.py
│     │  ├─ esp_adapter.py
│     │  └─ linux_adapter.py
│     ├─ tools/
│     │  ├─ __init__.py
│     │  ├─ init_project.py
│     │  ├─ check_ioc.py
│     │  ├─ generate_driver.py
│     │  ├─ review_code.py
│     │  ├─ fix_code.py
│     │  ├─ build_project.py
│     │  ├─ flash_project.py
│     │  ├─ monitor_project.py
│     │  ├─ parse_doc.py
│     │  └─ update_skill.py
│     ├─ workflows/
│     │  ├─ __init__.py
│     │  ├─ assemble_workflow.py
│     │  ├─ debug_loop_workflow.py
│     │  └─ skill_evolution_workflow.py
│     └─ utils/
│        ├─ __init__.py
│        ├─ paths.py
│        ├─ subprocess_utils.py
│        ├─ text_utils.py
│        └─ rule_utils.py
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  └─ fixtures/
├─ projects/
├─ driver_library/
└─ skill_library/
   └─ protocols/
```

---

## 3. 模块边界

### 3.1 `models/`

只放数据模型，不放业务逻辑。

首批模型：
- `ReviewIssue`
- `ReviewReport`
- `BuildResult`
- `FlashResult`
- `MonitorResult`
- `ProjectConfig`
- `DriverMetadata`
- `SkillArtifact`
- `AgentState`

要求：
- 所有模型都可 `model_dump()` / `model_validate()`
- 所有时间字段统一 `datetime`
- 所有路径字段统一存字符串，不直接存 `Path`

### 3.2 `core/`

只放稳定业务逻辑。

约束：
- 不依赖 CLI 参数解析库
- 不依赖具体 LLM 框架的上层 Agent 封装
- 尽量返回结构化模型，少返回裸字符串

### 3.3 `tools/`

每个 tool 只做一件事：
- 参数整理
- 调用 `core`
- 返回结构化结果

不要把状态机写进 `tools/`。

### 3.4 `workflows/`

只负责编排：
- 节点输入输出
- 条件分支
- 错误恢复
- fix loop 次数控制

### 3.5 `platforms/`

平台差异统一收口到这里：
- 构建命令
- 烧录命令
- 监控命令
- 工程配置检查
- 平台特有 review 规则

核心层不得直接写死 `openocd`、`idf.py`、`cmake` 路径分支。

---

## 4. 首批文件骨架

建议先创建这些文件并跑通 import：

```text
src/luxar/__init__.py
src/luxar/cli.py
src/luxar/models/schemas.py
src/luxar/core/config_manager.py
src/luxar/core/logger.py
src/luxar/core/project_manager.py
src/luxar/core/backup_manager.py
src/luxar/core/git_manager.py
src/luxar/core/lock_manager.py
src/luxar/core/platform_adapter.py
src/luxar/platforms/stm32_adapter.py
src/luxar/core/build_system.py
src/luxar/core/flash_system.py
src/luxar/core/uart_monitor.py
src/luxar/tools/init_project.py
src/luxar/tools/build_project.py
src/luxar/tools/flash_project.py
src/luxar/tools/monitor_project.py
```

首轮不需要全部实现完整逻辑，但要求：
- 能被测试导入
- 有清晰方法签名
- 有 `TODO(stage-x)` 风格注释标出后续实现点

---

## 5. 分阶段开发顺序

## Stage 0: 工程初始化

目标：
- 仓库可安装
- CLI 可启动
- 基本目录结构存在

任务：
1. 创建 `pyproject.toml`
2. 创建包结构 `src/luxar/`
3. 接入 `click`
4. 实现 `agent --help`
5. 实现 `agent config --show`

验收：
- `pip install -e .` 成功
- `agent --help` 成功
- `agent config --show` 能打印默认配置

## Stage 1: 项目骨架与本地安全能力

目标：
- 能初始化 STM32 项目目录
- 有日志、锁、快照、Git 基础能力

任务：
1. `ProjectManager.create_project()`
2. `AgentLogger`
3. `ProjectLock`
4. `BackupManager`
5. `GitManager`
6. `agent init`
7. `agent snapshot`
8. `agent diff`

验收：
- `projects/<name>/` 目录结构正确
- 可生成 `App/`、`logs/`、`.agent_backups/`
- 可创建快照并查看 diff
- agent 提交带 `Agent-Commit: true`

## Stage 2: STM32 最小闭环

目标：
- 只面向 STM32CubeMX
- 不做驱动库，只做最小 App 组装

任务：
1. `STM32CubeMXAdapter.check_project_config()`
2. `BuildSystem.build()`
3. `FlashSystem.flash()`
4. `UartMonitor.monitor()`
5. `agent build`
6. `agent flash`
7. `agent monitor`
8. `agent assemble`

验收：
- 能检查 `.ioc` 是否存在
- 能编译已有 CubeMX 工程
- 能烧录
- 能读取串口若干行输出

## Stage 3: Review Layer 基础

目标：
- 先落 Layer 2
- 再接 clang-tidy

任务：
1. `ReviewEngine._run_custom_rules()`
2. `EMB-001` 到 `EMB-010`
3. `ReviewEngine._parse_clang_tidy_output()`
4. `agent review`
5. `agent build --skip-review`
6. `agent build` 默认 review

验收：
- 违规代码可被结构化报告识别
- error/critical 会阻塞 build
- warning 不阻塞，但记录日志

## Stage 4: Generate → Review → Fix Loop

目标：
- 先不接 PDF
- 用摘要输入生成驱动

任务：
1. `tools/generate_driver.py`
2. `prompts/driver_generation.py`
3. `prompts/fix_code.py`
4. `assemble_workflow.py`
5. `fix` 节点重试上限

验收：
- 输入协议摘要可生成 `.h/.c`
- review 失败会进入 fix
- 达到最大修复次数会中断并给人工审查提示

## Stage 5: Driver Library

目标：
- 驱动可入库和检索

任务：
1. `DriverLibrary.store_driver()`
2. `DriverLibrary.search_driver()`
3. SQLite 索引
4. 驱动元数据记录

验收：
- 同一协议多个器件可检索
- 驱动带 review 状态和来源记录

## Stage 6: PDF 解析与知识库

目标：
- 文档可解析为摘要，供驱动生成使用

任务：
1. `PDFParser`
2. `KnowledgeBase`
3. `agent parse-doc`
4. 检索式摘要输出

验收：
- 能提取 datasheet 文本
- 能检索寄存器/时序片段

## Stage 7: 语义审查

目标：
- Layer 3 接入 LLM JSON 审查输出

任务：
1. `prompts/semantic_review.py`
2. `ReviewEngine._run_semantic_review()`
3. `ReviewEngine._parse_llm_review()`
4. 失败兜底

验收：
- LLM 返回异常 JSON 时不会让流程崩掉
- 可把 LLM 结果并入统一 `ReviewReport`

## Stage 8: Skill Evolution

目标：
- 成功项目自动沉淀协议 skill

任务：
1. `SkillManager`
2. `prompts/skill_evolution.py`
3. `update_skill` tool
4. `skill_evolution_workflow.py`
5. 版本化更新策略

验收：
- review+build+project_success 后生成 `SKILL.md`
- skill 写入协议通用知识而非项目私货
- skill 包含适用平台、runtime、验证来源、边界条件

## Stage 9: 多平台扩展

目标：
- 接入 ESP-IDF 与 LinuxHost

任务：
1. `ESPIDFAdapter`
2. `LinuxHostAdapter`
3. 平台选择逻辑
4. FreeRTOS / Linux 专项 review 规则

验收：
- 同一套 workflow 可切平台
- 核心层无 CubeMX 写死依赖

---

## 6. 每个核心模块的最小接口

```python
# core/platform_adapter.py
from abc import ABC, abstractmethod

class PlatformAdapter(ABC):
    @abstractmethod
    def check_project_config(self, project_path: str) -> dict: ...

    @abstractmethod
    def build(self, project_path: str, clean: bool = False) -> "BuildResult": ...

    @abstractmethod
    def flash(self, project_path: str, probe: str | None = None) -> "FlashResult": ...

    @abstractmethod
    def monitor(self, project_path: str, **kwargs) -> "MonitorResult": ...
```

```python
# core/build_system.py
class BuildSystem:
    def __init__(self, adapter: PlatformAdapter):
        self.adapter = adapter

    def build_project(self, project_path: str, clean: bool = False) -> BuildResult:
        return self.adapter.build(project_path, clean=clean)
```

```python
# core/review_engine.py
class ReviewEngine:
    def review_file(self, file_path: str, code: str | None = None) -> ReviewReport: ...
    def review_files(self, file_paths: list[str]) -> ReviewReport: ...
```

```python
# core/skill_manager.py
class SkillManager:
    def should_update_protocol_skill(self, review_passed: bool, build_success: bool, project_success: bool) -> bool: ...
    def update_protocol_skill(self, protocol: str, device_name: str, summary: str, lessons_learned: list[str], platforms: list[str], runtimes: list[str], source_project: str) -> SkillArtifact: ...
```

---

## 7. 任务分配粒度建议

为了让代理执行稳定，建议每次只下发一个可验证子任务。

推荐拆分粒度：
- Task A1: 初始化包结构与 `pyproject.toml`
- Task A2: 建 `schemas.py` 和基础模型
- Task A3: 建 `config_manager.py` 与默认 YAML
- Task A4: 建 `logger.py` / `lock_manager.py`
- Task A5: 建 `project_manager.py` / `backup_manager.py` / `git_manager.py`
- Task B1: 建 `PlatformAdapter` 与 `STM32CubeMXAdapter`
- Task B2: 建 `build_system.py` / `flash_system.py` / `uart_monitor.py`
- Task B3: 建 `agent init/build/flash/monitor`
- Task C1: 建 `review_engine.py` 的 custom rules
- Task C2: 建 clang-tidy 解析
- Task C3: 建 `agent review`
- Task D1: 建 driver generation prompt + tool
- Task D2: 建 fix loop workflow
- Task E1: 建 `driver_library.py`
- Task F1: 建 `pdf_parser.py` / `knowledge_base.py`
- Task G1: 建 semantic review
- Task H1: 建 `skill_manager.py`
- Task H2: 建 protocol skill 模板与 workflow
- Task I1: 建 `esp_adapter.py`
- Task I2: 建 `linux_adapter.py`

---

## 8. 测试顺序

不要一开始就追求全量 e2e。

推荐顺序：
1. unit test: 配置、模型、路径、规则
2. unit test: review engine 的自定义规则
3. integration test: STM32 项目初始化
4. integration test: 编译
5. hardware-in-the-loop: 烧录 + 串口
6. integration test: generate → review → fix
7. integration test: parse-doc → generate-driver
8. integration test: success → update-skill

---

## 9. 明确不在第一阶段做的事

这些内容先不要混进最小闭环：
- VS Code 插件 UI
- MCP Server 暴露
- 多协议总线自动推断
- 复杂 RAG 重排序
- 多设备并行烧录
- 自动修改 CubeMX 生成文件
- Linux 内核态驱动开发

---

## 10. 给执行代理的建议提示词

如果把任务继续交给 Codex/Kimi Code，建议每轮只下达一种类型的目标：

### 10.1 建骨架

```text
请根据 IMPLEMENTATION_PLAN.md 的 Stage 0 和 Stage 1，
直接创建项目骨架与基础模块文件。
要求：
1. 先不要实现完整业务逻辑，只建立目录、方法签名、Pydantic 模型和 CLI 入口
2. 所有文件都可 import
3. 使用 Python 3.11
4. 不要实现 ESP / Linux 部分
5. 完成后运行基础导入验证
```

### 10.2 做单模块

```text
请只实现 core/review_engine.py 的 Layer 2 自定义规则。
范围限制：
1. 只做 EMB-001 到 EMB-010
2. 不接 LLM
3. 不接 clang-tidy
4. 为每条规则补单元测试
5. 完成后汇报规则覆盖情况与未处理边界
```

### 10.3 做工作流

```text
请只实现 assemble_workflow.py，
完成 generate → review → fix → build 的 LangGraph 状态机。
要求：
1. review 不通过时进入 fix
2. fix 次数最多 3 次
3. build 成功且 protocol 非空时进入 update_skill
4. 不要扩展 CLI
```

---

## 11. 开工建议

最推荐的启动顺序是：

1. 先做 Stage 0 + Stage 1
2. 然后做 Stage 2 的 STM32 最小闭环
3. 再补 Review Layer
4. 最后才接 LLM 生成、RAG、Skill Evolution

这样做的原因是：没有稳定的本地工程能力、日志、快照、回滚、构建链之前，后面的“智能化”都会放大调试成本。



