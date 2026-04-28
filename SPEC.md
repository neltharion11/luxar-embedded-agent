以下是修改后的完整开发方案，已加入 Review 审查层 及多项关键改进。此文档可直接作为 `SPEC.md` 交给 Kimi Code 或 Codex 执行。

---

嵌入式智能 Agent 开发计划书 (SPEC.md) v1.2

版本: v1.2

技术栈: Python 3.11+, LangChain/LangGraph, Jinja2, SQLite, Chroma, OpenOCD, GCC ARM, CMake, clang-tidy, GitPython, portalocker

目标: 构建一个 CLI 驱动的嵌入式开发 Agent，实现文档解析→驱动生成→代码审查→项目组装→编译烧录→闭环调试→经验沉淀（Skill Evolution）的完整工作流。

---

1. 项目概述

1.1 核心目标
开发一个名为 `Luxar` 的 CLI 工具，实现以下闭环工作流：

```text
用户输入需求 → Agent 指导 CubeMX 配置 → 检查 .ioc → 解析 PDF 数据手册
→ 生成 MCU 无关设备驱动 → 代码审查（Review Layer）→ 修复 → 存入驱动库
→ 组装项目（业务代码 + 驱动）→ CMake 编译 → OpenOCD 烧录 → 串口监控
→ 自动诊断修复
```

1.2 关键设计决策（已确认）

决策项	方案
HAL 策略	纯 HAL 封装，不直接操作寄存器
CubeMX 集成	Agent 生成配置指南 → 用户手动配置 → Agent 只检查 .ioc，不修改 CubeMX 生成的 `Core/` 目录
用户代码位置	所有 Agent 生成代码必须位于 `App/` 目录，通过 `main.c` 的 `USER CODE BEGIN/END` 安全区调用
驱动库组织	方案 A：按 `driver_library/<vendor>/<device>/` 组织，MCU 无关，通过函数指针注入 HAL
交互方式	CLI 为主，后续封装 VS Code 插件
生成深度	可编译项目，Agent 自主完成编译-烧录-串口监控-修复闭环
代码审查	强制 Review Layer：所有 AI 生成代码必须经过自动审查 + LLM 语义审查，通过后方可入库/编译
平台扩展	架构从 Day 1 按 `platform adapter` 抽象，STM32(CubeMX) 只是首个落地适配器，后续支持 ESP-IDF、Raspberry Pi/Linux、FreeRTOS
自我进化	驱动 review 通过且项目闭环成功后，自动抽取协议知识，编写/更新可复用 skill，供后续同协议器件复用

---

2. 系统架构（v1.2 含 Review Layer + Skill Evolution）

```text
┌─────────────────────────────────────────────┐
│  CLI Layer (click)                          │
│  - 命令解析、参数校验、交互提示               │
├─────────────────────────────────────────────┤
│  Workflow Layer (LangGraph StateGraph)      │
│  - 状态机编排：生成 → 审查 → 修复 → 编译    │
│  - 支持条件分支：审查失败则循环修复           │
│  - 支持平台差异化流程：STM32 / ESP / Linux   │
├─────────────────────────────────────────────┤
│  Review Layer（新增）                        │
│  ├─ Static Analyzer: clang-tidy / cppcheck  │
│  ├─ Rule Engine: 自定义规则检查器             │
│  ├─ CubeMX Safety Guard: 安全区边界检查      │
│  ├─ LLM Semantic Review: 语义级代码审查      │
│  └─ Review Report: 结构化审查报告             │
├─────────────────────────────────────────────┤
│  Skill Evolution Layer（新增）               │
│  ├─ Protocol Knowledge Extractor            │
│  ├─ Skill Writer / Updater                  │
│  ├─ Validation Gate                         │
│  └─ Skill Registry                          │
├─────────────────────────────────────────────┤
│  Tool Layer (LangChain Tools)               │
│  - 原子操作：init_project, check_ioc,       │
│    generate_driver, review_code, fix_code,  │
│    build_project, flash_project,             │
│    update_protocol_skill, ...               │
├─────────────────────────────────────────────┤
│  Core Layer (Business Logic)                │
│  - ProjectManager, DriverLibrary,           │
│    CubeMXParser, CodeGenerator,             │
│    BuildSystem, UartMonitor,                │
│    ReviewEngine, GitManager,                │
│    ConfigManager, Logger,                   │
│    PlatformAdapter, SkillManager            │
├─────────────────────────────────────────────┤
│  Template Layer (Jinja2)                    │
│  - 驱动模板、业务代码模板、CMakeLists 模板    │
├─────────────────────────────────────────────┤
│  External Tools (Subprocess)                │
│  - cmake, make, openocd, arm-none-eabi-gcc  │
│  - clang-tidy, cppcheck, git, idf.py        │
│  - platformio, west, serial monitor         │
└─────────────────────────────────────────────┘
```

---

2.1 平台适配原则（新增）

为保证后续可扩展到 ESP 与 Raspberry Pi，工作流不得把 CubeMX 假设写死在核心层，必须通过平台适配器抽象：

```text
PlatformAdapter
 ├─ STM32CubeMXAdapter
 │   - check_project_config(.ioc)
 │   - validate_user_code_boundary()
 │   - build_with_cmake()
 │   - flash_with_openocd()
 ├─ ESPIDFAdapter
 │   - check_project_config(sdkconfig/CMakeLists.txt)
 │   - build_with_idf.py()
 │   - flash_with_idf.py()
 │   - monitor_uart()
 └─ LinuxHostAdapter (Raspberry Pi)
     - check_project_config(CMakeLists.txt/Makefile/systemd)
     - build_native()
     - deploy_local_or_ssh()
     - monitor_journal_or_stdio()
```

约束：
- `ReviewEngine` 不依赖具体平台目录结构。
- `BuildSystem` / `FlashSystem` / `MonitorSystem` 只能通过 `PlatformAdapter` 调用底层工具。
- STM32 的 `CubeMX Safety Guard` 是 STM32 专属规则，不得污染 ESP/Linux 流程。
- FreeRTOS 与 Linux 作为 runtime profile 处理，不与硬件平台强绑定。

2.2 Runtime Profile（新增）

```text
RuntimeProfile
 ├─ baremetal
 ├─ freertos
 └─ linux
```

运行时配置决定：
- 并发规则集是否启用
- 日志/监控方式
- 线程任务模型审查项
- 生成模板类型

---

3. Review Layer 详细设计（核心新增）

3.1 为什么必须加入 Review Layer

研究表明，45% 的 AI 生成代码包含安全缺陷。对于嵌入式系统，AI 生成的代码在以下方面风险极高：
- 空指针解引用、缓冲区溢出
- 中断处理不当
- 硬件时序违规
- 违反 MISRA-C 规范
- 错误注入到 CubeMX 安全区

Review Layer 作为强制性质量门控，所有代码必须通过审查才能进入下一阶段。

3.2 四层审查机制

```text
生成代码
    │
    ▼
┌─────────────────────────────────────────┐
│ Layer 1: 语法与静态分析                 │
│ 工具: clang-tidy / cppcheck            │
│ 检测: 语法错误、未初始化变量、死代码、   │
│       除零、缓冲区溢出、MISRA 违规       │
└─────────┬───────────────────────────────┘
          │ 通过
          ▼
┌─────────────────────────────────────────┐
│ Layer 2: 规则引擎检查                   │
│ 检测: 命名规范、头文件保护、接口一致性、  │
│       CubeMX 安全区边界、HAL 使用规范    │
└─────────┬───────────────────────────────┘
          │ 通过
          ▼
┌─────────────────────────────────────────┐
│ Layer 3: LLM 语义审查                   │
│ 检测: 逻辑错误、时序合理性、资源泄漏、    │
│       并发安全、错误处理完整性           │
└─────────┬───────────────────────────────┘
          │ 通过
          ▼
┌─────────────────────────────────────────┐
│ Layer 4: 人工确认（可选）               │
│ 高置信度项目可跳过，安全关键项目强制      │
└─────────┬───────────────────────────────┘
          ▼
      入库 / 编译
```

3.3 审查规则清单

Layer 1: 静态分析规则（clang-tidy）

```yaml
# .clang-tidy 配置（Agent 自动生成）
Checks: >
  clang-analyzer-*,
  cppcoreguidelines-*,
  bugprone-*,
  performance-*,
  portability-*,
  readability-*,
  -cppcoreguidelines-avoid-magic-numbers,
  -readability-named-parameter

CheckOptions:
  - key:   readability-identifier-naming.FunctionCase
    value: lower_case
  - key:   readability-identifier-naming.MacroDefinitionCase
    value: UPPER_CASE
  - key:   readability-identifier-naming.TypedefCase
    value: lower_case
  - key:   readability-identifier-naming.StructCase
    value: lower_case
  - key:   readability-identifier-naming.EnumCase
    value: lower_case
  - key:   readability-identifier-naming.ConstantCase
    value: UPPER_CASE
  - key:   readability-identifier-naming.VariableCase
    value: lower_case
```

Layer 2: 自定义规则引擎

规则 ID	检查项	严重程度	说明
`EMB-001`	禁止直接引用 `hspi1/hi2c1` 等全局句柄	Error	驱动库必须接口注入
`EMB-002`	禁止修改 `Core/` 目录下非 USER CODE 区域	Error	CubeMX 安全区红线
`EMB-003`	所有函数必须有 Doxygen 注释	Warning	文档规范
`EMB-004`	禁止在驱动中使用 `printf`	Error	驱动层无标准输出
`EMB-005`	所有指针参数必须做空指针检查	Error	防御式编程
`EMB-006`	禁止硬编码寄存器地址	Error	必须通过宏定义
`EMB-007`	中断服务函数中禁止调用阻塞 HAL 函数	Error	实时性要求
`EMB-008`	堆内存分配（malloc/free）检测	Warning	嵌入式优先用静态分配
`EMB-009`	函数圈复杂度不超过 15	Warning	可维护性
`EMB-010`	每个 `.c` 文件必须有对应的 `.h` 文件	Error	模块化要求

Layer 3: LLM 语义审查 Prompt 模板

```python
SEMANTIC_REVIEW_PROMPT = """
你是一位拥有 20 年经验的嵌入式软件架构师，正在审查以下由 AI 生成的 C 代码。

审查维度：
1. **逻辑正确性**：初始化顺序是否正确？时序是否满足 datasheet 要求？
2. **资源管理**：是否有内存泄漏？句柄是否正确释放？
3. **并发安全**：中断与主循环的资源竞争是否处理？
4. **错误处理**：所有错误路径是否都被处理？返回值是否被检查？
5. **HAL 规范**：是否正确使用 HAL 的 Timeout 机制？DMA 配置是否合理？
6. **可移植性**：是否包含平台相关硬编码？

代码：
```c
__CODE_BLOCK__
```

请输出 JSON 格式审查报告：
{{
"passed": bool,
"issues": [
{{
"severity": "critical|warning|info",
"line": int,
"rule": "逻辑错误|时序违规|资源泄漏|...",
"description": "具体问题描述",
"suggestion": "修复建议"
}}
],
"summary": "总体评价"
}}
"""

def build_semantic_review_prompt(source: str) -> str:
    """
    避免使用 str.format 直接插入 C 代码。
    C 源码中的花括号会破坏模板解析，因此使用唯一哨兵字符串替换。
    """
    return SEMANTIC_REVIEW_PROMPT.replace("__CODE_BLOCK__", source)
```

3.4 ReviewEngine 核心实现

```python
# core/review_engine.py
from pydantic import BaseModel
from typing import List, Literal
from pathlib import Path
import subprocess
import json

class ReviewIssue(BaseModel):
    file: str
    line: int
    column: int
    severity: Literal["critical", "error", "warning", "info"]
    rule_id: str
    message: str
    suggestion: str = ""

class ReviewReport(BaseModel):
    passed: bool
    total_issues: int
    critical_count: int
    error_count: int
    warning_count: int
    issues: List[ReviewIssue]
    raw_logs: dict = {}

class ReviewEngine:
    def __init__(self, project_path: str):
        self.project_path = Path(project_path)
        self.clang_tidy_config = self.project_path / ".clang-tidy"

    def review_file(self, file_path: str, code: str = None) -> ReviewReport:
        """
        对单个文件执行完整审查流程。
        1. 写入临时文件（如 code 参数提供）
        2. 运行 clang-tidy
        3. 运行自定义规则引擎
        4. 运行 LLM 语义审查（可选，针对驱动文件）
        5. 聚合报告
        """
        path = Path(file_path)

        # Layer 1: clang-tidy
        static_report = self._run_clang_tidy(path)

        # Layer 2: 自定义规则
        custom_report = self._run_custom_rules(path, code)

        # Layer 3: LLM 语义审查（仅对 App/ 或 drivers/ 下的文件）
        if self._should_run_semantic_review(path):
            semantic_report = self._run_semantic_review(path, code)
        else:
            semantic_report = ReviewReport(passed=True, total_issues=0,
                                           critical_count=0, error_count=0,
                                           warning_count=0, issues=[])

        # 聚合
        return self._merge_reports(static_report, custom_report, semantic_report)

    def _run_clang_tidy(self, path: Path) -> ReviewReport:
        """调用 clang-tidy 进行静态分析"""
        cmd = [
            "clang-tidy", str(path),
            "--",
            "-mcpu=cortex-m3", "-mthumb",
            "-I", str(self.project_path / "Core/Inc"),
            "-I", str(self.project_path / "Drivers/STM32F1xx_HAL_Driver/Inc")
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        # 解析 clang-tidy 输出...
        return self._parse_clang_tidy_output(result.stdout)

    def _path_parts_lower(self, path: Path) -> tuple[str, ...]:
        return tuple(part.lower() for part in path.parts)

    def _should_run_semantic_review(self, path: Path) -> bool:
        parts = self._path_parts_lower(path)
        return "app" in parts or "drivers" in parts

    def _run_custom_rules(self, path: Path, code: str = None) -> ReviewReport:
        """运行自定义规则引擎"""
        source = code or path.read_text(encoding="utf-8")
        issues = []
        parts = self._path_parts_lower(path)

        # EMB-001: 禁止直接引用全局句柄
        if "hspi1" in source or "hi2c1" in source:
            issues.append(ReviewIssue(
                file=str(path), line=0, column=0,
                severity="error", rule_id="EMB-001",
                message="驱动代码中禁止直接引用 CubeMX 生成的全局句柄",
                suggestion="使用接口注入模式，通过函数指针传入 HAL 操作"
            ))

        # EMB-002: 安全区检查
        if path.name == "main.c":
            if "USER CODE BEGIN" not in source:
                issues.append(ReviewIssue(
                    file=str(path), line=0, column=0,
                    severity="error", rule_id="EMB-002",
                    message="main.c 中缺少 USER CODE 安全区标记",
                    suggestion="确保 CubeMX 生成的安全区标签存在"
                ))

        # EMB-004: 禁止 printf
        if "drivers" in parts and "printf" in source:
            issues.append(ReviewIssue(
                file=str(path), line=0, column=0,
                severity="error", rule_id="EMB-004",
                message="设备驱动层禁止使用 printf",
                suggestion="使用返回值或回调函数报告错误"
            ))

        # 更多规则...

        critical = sum(1 for i in issues if i.severity == "critical")
        errors = sum(1 for i in issues if i.severity == "error")
        warnings = sum(1 for i in issues if i.severity == "warning")

        return ReviewReport(
            passed=(critical == 0 and errors == 0),
            total_issues=len(issues),
            critical_count=critical,
            error_count=errors,
            warning_count=warnings,
            issues=issues
        )

    def _run_semantic_review(self, path: Path, code: str = None) -> ReviewReport:
        """调用 LLM 进行语义级审查"""
        source = code or path.read_text(encoding="utf-8")
        prompt = build_semantic_review_prompt(source)
        response = llm_client.complete(prompt)
        return self._parse_llm_review(response)

    def _merge_reports(self, *reports: ReviewReport) -> ReviewReport:
        """合并多层审查报告"""
        all_issues = []
        for r in reports:
            all_issues.extend(r.issues)

        critical = sum(1 for i in all_issues if i.severity == "critical")
        errors = sum(1 for i in all_issues if i.severity == "error")

        return ReviewReport(
            passed=(critical == 0 and errors == 0),
            total_issues=len(all_issues),
            critical_count=critical,
            error_count=errors,
            warning_count=sum(1 for i in all_issues if i.severity == "warning"),
            issues=all_issues,
            raw_logs={}
        )
```

3.5 审查工作流集成

在 LangGraph 状态机中，Review 是一个条件节点：

```python
from langgraph.graph import StateGraph, END

class AgentState(TypedDict):
    project_name: str
    generated_files: List[str]
    review_report: Optional[ReviewReport]
    fix_iteration: int
    max_fix_iterations: int

def generate_code(state: AgentState):
    # 生成驱动/业务代码
    return {"generated_files": [...]}

def review_code(state: AgentState):
    engine = ReviewEngine(project_path)
    report = ReviewReport(
        passed=True,
        total_issues=0,
        critical_count=0,
        error_count=0,
        warning_count=0,
        issues=[],
        raw_logs={}
    )

    for f in state["generated_files"]:
        r = engine.review_file(f)
        report = engine._merge_reports(report, r)

    return {"review_report": report}

def fix_code(state: AgentState):
    # LLM 根据 review_report 生成修复补丁
    if state["fix_iteration"] >= state["max_fix_iterations"]:
        raise Exception("达到最大修复次数，请人工审查")

    # 调用 LLM 修复
    fixed = llm_fix(state["generated_files"], state["review_report"])
    return {
        "generated_files": fixed,
        "fix_iteration": state["fix_iteration"] + 1
    }

def should_fix(state: AgentState):
    if state["review_report"].passed:
        return "compile"
    return "fix"

def update_skill(state: AgentState):
    if not state.get("project_success"):
        return {}
    artifact = skill_manager.update_protocol_skill(
        protocol=state["protocol"],
        device_name=state["project_name"],
        summary="Extracted from validated project workflow",
        lessons_learned=["review passed", "build passed", "project success"],
        platforms=[state["platform"]],
        runtimes=[state["runtime"]],
        source_project=state["project_name"]
    )
    return {"skill_artifact": artifact.model_dump()}

# 构建 Graph
workflow = StateGraph(AgentState)
workflow.add_node("generate", generate_code)
workflow.add_node("review", review_code)
workflow.add_node("fix", fix_code)
workflow.add_node("compile", build_project)
workflow.add_node("update_skill", update_skill)

workflow.add_edge("generate", "review")
workflow.add_conditional_edges(
    "review",
    should_fix,
    {"fix": "fix", "compile": "compile"}
)
workflow.add_edge("fix", "review")  # 修复后重新审查
workflow.add_conditional_edges(
    "compile",
    lambda state: "update_skill" if state.get("project_success") and state.get("protocol") else "end",
    {"update_skill": "update_skill", "end": END}
)
workflow.add_edge("update_skill", END)
```

3.6 Skill Evolution 集成（新增）

当以下条件全部满足时，Agent 必须尝试沉淀协议级 skill：
- 驱动代码通过 Review Layer
- 项目完成组装并至少一次构建成功
- 若存在硬件闭环流程，则烧录/监控结果达到最低可用标准

沉淀目标不是复制当前项目，而是抽象出“协议/器件族通用知识”，例如：
- SPI 传感器寄存器读写握手模式
- I2C 设备探测、重试、超时、ACK 异常处理
- UART 类设备的帧同步、CRC、状态机
- FreeRTOS 下驱动与任务/中断协作约束
- Linux 用户态驱动封装与设备节点访问模式

产物：
- `skill_library/protocols/<protocol>/<skill_name>/SKILL.md`
- 可选参考模板、检查清单、示例适配代码
- skill 元数据索引（来源项目、验证次数、适用平台、适用 runtime）

```python
# core/skill_manager.py
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List

class SkillArtifact(BaseModel):
    name: str
    protocol: str
    platforms: List[str] = Field(default_factory=list)
    runtimes: List[str] = Field(default_factory=list)
    source_projects: List[str] = Field(default_factory=list)
    validation_count: int = 0
    path: str

class SkillManager:
    def __init__(self, skill_library: str):
        self.skill_library = Path(skill_library)
        self.skill_library.mkdir(parents=True, exist_ok=True)

    def should_update_protocol_skill(
        self,
        review_passed: bool,
        build_success: bool,
        project_success: bool
    ) -> bool:
        return review_passed and build_success and project_success

    def update_protocol_skill(
        self,
        protocol: str,
        device_name: str,
        summary: str,
        lessons_learned: list[str],
        platforms: list[str],
        runtimes: list[str],
        source_project: str
    ) -> SkillArtifact:
        """
        写入或更新协议级 skill，而不是项目专属脚本。
        skill 内容包括：
        1. 协议特性
        2. 常见初始化/读写时序
        3. 典型错误模式
        4. review 检查清单
        5. 平台适配差异（STM32 / ESP / Linux）
        """
        skill_dir = self.skill_library / "protocols" / protocol.lower() / f"{protocol.lower()}_generic"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(self._render_skill_markdown(
            protocol=protocol,
            device_name=device_name,
            summary=summary,
            lessons_learned=lessons_learned,
            platforms=platforms,
            runtimes=runtimes
        ), encoding="utf-8")
        return SkillArtifact(
            name=f"{protocol.lower()}_generic",
            protocol=protocol,
            platforms=platforms,
            runtimes=runtimes,
            source_projects=[source_project],
            validation_count=1,
            path=str(skill_file)
        )
```

建议策略：
- 第一版先只自动维护协议 skill，不自动改已有“平台 skill”。
- skill 更新必须追加“验证来源”和“适用边界”，避免错误经验扩散。
- 若审查通过但运行闭环失败，只允许写入临时草稿，不升级为正式 skill。

---

4. 关键改进补充

4.1 Git 版本控制集成

所有 Agent 操作必须可追踪、可回滚。

```python
# core/git_manager.py
import git
from datetime import datetime
from typing import List

class GitManager:
    def __init__(self, repo_path: str):
        self.repo = git.Repo(repo_path)

    def create_branch(self, branch_name: str):
        """Agent 在新分支上工作，避免污染主分支"""
        self.repo.create_head(branch_name)
        self.repo.heads[branch_name].checkout()

    def commit_agent_action(self, action: str, files: List[str], base_commit: str | None = None):
        """
        所有 Agent 提交都带 trailer，便于后续识别 agent commit 链。
        """
        self.repo.index.add(files)
        message = (
            f"[Agent] {action} | {datetime.now().isoformat()}\n\n"
            f"Auto-generated by Luxar\n"
            f"Files: {', '.join(files)}\n"
            f"Agent-Commit: true\n"
        )
        if base_commit:
            message += f"Agent-Base-Commit: {base_commit}\n"
        self.repo.index.commit(message)

    def find_last_human_commit(self) -> str | None:
        """
        倒序查找最近一个不包含 Agent-Commit trailer 的提交，作为人工基线。
        """
        for commit in self.repo.iter_commits():
            if "Agent-Commit: true" not in commit.message:
                return commit.hexsha
        return None

    def rollback_last_agent_commit(self):
        """
        仅回滚最近一次 agent commit。
        前提：工作区必须干净，避免误伤人工修改。
        """
        if self.repo.is_dirty(untracked_files=True):
            raise RuntimeError("工作区存在未提交修改，禁止自动回滚")

        head = self.repo.head.commit
        if "Agent-Commit: true" not in head.message:
            raise RuntimeError("当前 HEAD 不是 agent commit，禁止自动回滚")

        self.repo.git.revert(head.hexsha, no_edit=True)

    def get_diff_since_last_human_commit(self) -> str:
        """获取自最近一次人工提交以来的所有 agent 变更"""
        base = self.find_last_human_commit()
        if base is None:
            return self.repo.git.diff()
        return self.repo.git.diff(base, "HEAD")
```

Git 工作流规范：
- Agent 默认在 `agent/<task-name>` 分支工作
- 每次生成/修改代码后自动 `git commit`
- 编译失败或审查不通过时，可 `agent rollback --project <name>` 一键回滚
- 人工确认无误后，合并到 `main` 分支

4.2 配置管理系统

避免硬编码，所有可配置项集中管理。

```yaml
# config/luxar.yaml
agent:
  name: "Luxar"
  version: "0.2.0"
  workspace: "./workspace/projects"
  driver_library: "./workspace/driver_library"
  skill_library: "./skill_library"

llm:
  provider: "deepseek"  # deepseek / openai / claude
  model: "deepseek-chat"
  temperature: 0.2
  max_tokens: 4096

review:
  enabled: true
  layers:
    static_analysis: true
    custom_rules: true
    semantic_review: true
  max_fix_iterations: 3
  fail_on_warning: false

platform:
  default_platform: "stm32cubemx"   # stm32cubemx / esp-idf / linux-host
  default_runtime: "baremetal"      # baremetal / freertos / linux

build:
  toolchain_prefix: "arm-none-eabi-"
  cmake_generator: "Unix Makefiles"
  jobs: 4

flash:
  default_probe: "stlink"
  openocd_interface: "interface/stlink.cfg"
  openocd_target: "target/stm32f1x.cfg"

monitor:
  default_baudrate: 115200
  default_timeout: 10
  auto_release_port: true

git:
  auto_commit: true
  agent_branch_prefix: "agent/"

evolution:
  enabled: true
  auto_update_protocol_skill: true
  require_project_success: true
  min_review_passes: 1
```

```python
# core/config_manager.py
import yaml
from pydantic import BaseModel, Field

class AgentSection(BaseModel):
    name: str = "Luxar"
    version: str = "0.2.0"
    workspace: str = "./workspace/projects"
    driver_library: str = "./workspace/driver_library"
    skill_library: str = "./skill_library"

class LLMSection(BaseModel):
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    temperature: float = 0.2
    max_tokens: int = 4096

class ReviewLayers(BaseModel):
    static_analysis: bool = True
    custom_rules: bool = True
    semantic_review: bool = True

class ReviewSection(BaseModel):
    enabled: bool = True
    layers: ReviewLayers = Field(default_factory=ReviewLayers)
    max_fix_iterations: int = 3
    fail_on_warning: bool = False

class PlatformSection(BaseModel):
    default_platform: str = "stm32cubemx"
    default_runtime: str = "baremetal"

class BuildSection(BaseModel):
    toolchain_prefix: str = "arm-none-eabi-"
    cmake_generator: str = "Unix Makefiles"
    jobs: int = 4

class FlashSection(BaseModel):
    default_probe: str = "stlink"
    openocd_interface: str = "interface/stlink.cfg"
    openocd_target: str = "target/stm32f1x.cfg"

class MonitorSection(BaseModel):
    default_baudrate: int = 115200
    default_timeout: int = 10

class GitSection(BaseModel):
    auto_commit: bool = True
    agent_branch_prefix: str = "agent/"

class EvolutionSection(BaseModel):
    enabled: bool = True
    auto_update_protocol_skill: bool = True
    require_project_success: bool = True
    min_review_passes: int = 1

class AgentConfig(BaseModel):
    agent: AgentSection = Field(default_factory=AgentSection)
    llm: LLMSection = Field(default_factory=LLMSection)
    review: ReviewSection = Field(default_factory=ReviewSection)
    platform: PlatformSection = Field(default_factory=PlatformSection)
    build: BuildSection = Field(default_factory=BuildSection)
    flash: FlashSection = Field(default_factory=FlashSection)
    monitor: MonitorSection = Field(default_factory=MonitorSection)
    git: GitSection = Field(default_factory=GitSection)
    evolution: EvolutionSection = Field(default_factory=EvolutionSection)

    @classmethod
    def load(cls, path: str = "./config/luxar.yaml") -> "AgentConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)
```

4.3 日志与审计系统

嵌入式调试高度依赖可追溯的日志。

```python
# core/logger.py
import logging
import json
from datetime import datetime
from pathlib import Path

class AgentLogger:
    def __init__(self, log_dir: str = "./agent_workspace/logs"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 文件日志
        self.file_handler = logging.FileHandler(
            self.log_dir / f"agent_{datetime.now().strftime('%Y%m%d')}.log"
        )
        self.file_handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)s | %(name)s | %(message)s'
        ))

        # 结构化事件日志（JSON Lines）
        self.event_log = self.log_dir / "events.jsonl"

        self.logger = logging.getLogger("Luxar")
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(self.file_handler)

    def log_event(self, event_type: str, project: str, details: dict):
        """记录结构化事件，用于后续审计和调试"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": event_type,
            "project": project,
            "details": details
        }
        with open(self.event_log, 'a') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        self.logger.info(f"[{event_type}] {project}: {details}")

    def log_review(self, project: str, report: ReviewReport):
        """记录审查结果"""
        self.log_event("CODE_REVIEW", project, {
            "passed": report.passed,
            "critical": report.critical_count,
            "errors": report.error_count,
            "warnings": report.warning_count,
            "issues": [i.dict() for i in report.issues]
        })

    def log_build(self, project: str, result: BuildResult):
        """记录编译结果"""
        self.log_event("BUILD", project, {
            "success": result.success,
            "flash_kb": result.flash_used_kb,
            "ram_kb": result.ram_used_kb,
            "errors": len(result.errors),
            "warnings": len(result.warnings)
        })

    def log_flash(self, project: str, success: bool, log: str):
        self.log_event("FLASH", project, {"success": success, "log": log[:500]})

    def log_uart(self, project: str, has_data: bool, lines: int):
        self.log_event("UART_MONITOR", project, {"has_data": has_data, "lines": lines})
```

4.4 并发安全与项目隔离

防止多项目同时操作导致状态混乱。

```python
# core/lock_manager.py
import os
from pathlib import Path
import portalocker

class ProjectLock:
    """跨平台文件锁，确保同一项目不会被多个 Agent 实例并发操作"""

    def __init__(self, project_path: str):
        self.lock_file = Path(project_path) / ".agent.lock"
        self.fd = None

    def __enter__(self):
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.lock_file, "a+", encoding="utf-8")
        portalocker.lock(self.fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
        self.fd.seek(0)
        self.fd.truncate()
        self.fd.write(str(os.getpid()))
        self.fd.flush()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd:
            portalocker.unlock(self.fd)
            self.fd.close()

# 使用方式
with ProjectLock(project_path):
    # 执行生成/编译/烧录等操作
    pass
```

4.5 备份与回滚机制

Agent 修改代码前自动备份，支持一键回滚。

```python
# core/backup_manager.py
import shutil
from datetime import datetime
from pathlib import Path

class BackupManager:
    def __init__(self, project_path: str):
        self.project = Path(project_path)
        self.backup_dir = self.project / ".agent_backups"
        self.backup_dir.mkdir(exist_ok=True)

    def create_snapshot(self, label: str) -> Path:
        """创建项目快照"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_name = f"{timestamp}_{label}"
        snapshot_path = self.backup_dir / snapshot_name
        snapshot_path.mkdir(parents=True, exist_ok=False)

        # 只备份 Agent 可管理内容，不备份 Core/（CubeMX 生成）
        backup_items = ["App", "CMakeLists.txt"]
        for item in backup_items:
            src = self.project / item
            if src.exists():
                dst = snapshot_path / item
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

        for ioc_file in self.project.glob("*.ioc"):
            shutil.copy2(ioc_file, snapshot_path / ioc_file.name)

        return snapshot_path

    def restore_snapshot(self, snapshot_path: Path):
        """恢复到指定快照"""
        managed_targets = ["App", "CMakeLists.txt"]
        for name in managed_targets:
            dst = self.project / name
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()

        for ioc_file in self.project.glob("*.ioc"):
            ioc_file.unlink()

        for item in snapshot_path.iterdir():
            dst = self.project / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)

    def list_snapshots(self) -> List[Path]:
        """列出所有快照"""
        return sorted(self.backup_dir.iterdir(), key=lambda p: p.name)
```

4.6 LLM Prompt 工程规范

嵌入式代码生成对准确性要求极高，Prompt 必须结构化。

```python
# prompts/driver_generation.py
DRIVER_GENERATION_PROMPT = """
你是一位资深嵌入式软件工程师（15年经验，精通 STM32 HAL 库和 MISRA-C:2012）。

任务：基于以下芯片信息，生成一个 MCU 无关的设备驱动。

【芯片信息】
- 芯片型号: {chip_name}
- 通信接口: {interface}
- 协议参数: {protocol_info}
- 关键寄存器: {registers}

【接口规范】（必须严格遵守）
1. 驱动必须定义 hal_interface_t 结构体，通过函数指针注入 HAL 操作
2. 禁止直接引用 hspi1 / hi2c1 / huart1 等 CubeMX 全局变量
3. 所有函数返回 int，0 表示成功，负值表示错误码
4. 所有指针参数必须做空指针检查
5. 禁止使用 printf / malloc / free
6. 必须包含完整的 Doxygen 注释
7. 代码必须符合 MISRA-C:2012 规则

【输出格式】
请输出两个代码块：
1. 头文件（.h）：包含接口定义、结构体、宏、错误码
2. 源文件（.c）：包含实现

【示例】
```c
// bmi270.h
typedef struct {
    int (*spi_tx_rx)(uint8_t *tx, uint8_t *rx, uint16_t len, uint32_t timeout);
    void (*cs_low)(void);
    void (*cs_high)(void);
    void (*delay_ms)(uint32_t);
} bmi270_hal_t;

int bmi270_init(bmi270_handle_t *dev, const bmi270_hal_t *hal);
```

请开始生成 {chip_name} 的驱动代码。
"""

FIX_PROMPT = """
你正在修复以下 C 代码中的问题。

【原始代码】

```c
{code}
```

【审查报告】
{review_report}

【修复要求】
1. 只修改有问题的部分，不要重构整个文件
2. 保持原有代码风格和命名规范
3. 修复后必须通过 MISRA-C 检查
4. 输出完整的修复后代码

请输出修复后的完整代码。
"""
```

---

5. 更新后的数据模型

5.1 ReviewReport（新增）

```python
# models/schemas.py
class ReviewIssue(BaseModel):
    file: str
    line: int
    column: int
    severity: Literal["critical", "error", "warning", "info"]
    rule_id: str
    message: str
    suggestion: str = ""

class ReviewReport(BaseModel):
    passed: bool
    total_issues: int
    critical_count: int
    error_count: int
    warning_count: int
    issues: List[ReviewIssue]
    raw_logs: dict = {}
    reviewed_at: datetime = Field(default_factory=datetime.now)
```

5.2 AgentState（LangGraph 状态）

```python
class AgentState(TypedDict):
    project_name: str
    project_config: ProjectConfig
    platform: str                 # stm32cubemx / esp-idf / linux-host
    runtime: str                  # baremetal / freertos / linux
    protocol: Optional[str]
    generated_files: List[str]
    review_report: Optional[ReviewReport]
    fix_iteration: int
    max_fix_iterations: int
    build_result: Optional[BuildResult]
    flash_result: Optional[dict]
    uart_result: Optional[dict]
    snapshot_path: Optional[str]  # 备份路径
    project_success: bool
    skill_artifact: Optional[dict]
```

---

6. 更新后的 CLI 命令

```bash
# 项目生命周期
agent init --name <项目名> --mcu <芯片型号>

# 文档与知识
agent parse-doc --project <项目名> --doc <PDF路径>

# 驱动管理（新增 --review 标志）
agent generate-driver --chip <芯片> --interface <SPI/I2C/UART> --doc-summary <摘要> [--platform <stm32cubemx|esp-idf|linux-host>] [--runtime <baremetal|freertos|linux>]
agent review-driver --driver-path <路径>          # 单独审查驱动
agent search-driver --keyword <关键词>
agent update-skill --project <项目名> --protocol <SPI/I2C/UART/CAN/...>
agent list-skills [--protocol <关键词>]

# 项目组装（内部自动触发 review）
agent assemble --project <项目名> --drivers <列表>

# 编译（编译前自动 review）
agent build --project <项目名> [--clean] [--skip-review]

# 烧录与监控
agent flash --project <项目名> --probe <stlink>
agent monitor --project <项目名> --port <串口>

# 闭环调试
agent debug-loop --project <项目名> --probe <调试器> --port <串口>

# 审查与修复（新增）
agent review --project <项目名> [--file <文件路径>]
agent fix --project <项目名> --report <审查报告路径>

# 版本控制（新增）
agent snapshot --project <项目名> --label <标签>
agent rollback --project <项目名> [--to <快照名>]
agent diff --project <项目名>

# 配置与诊断
agent config --show
agent status --project <项目名>
agent log --project <项目名> --type <review|build|flash|uart>
```

---

7. 更新后的 Sprint 计划

Sprint 1：骨架闭环（验证编译-烧录-串口）
目标：Agent 生成 LED 闪烁项目 → 编译 → 烧录 → 串口输出 "Hello Agent"

任务	模块	验收标准
T1	`models/schemas.py`	所有 Pydantic 模型可序列化
T2	`core/config_manager.py`	可从 YAML 加载配置
T3	`core/logger.py`	事件日志写入 JSON Lines
T4	`core/project_manager.py`	创建项目目录结构正确
T5	`core/backup_manager.py`	快照创建与恢复正常
T6	`core/git_manager.py`	Agent 提交自动带 `[Agent]` 前缀
T7	`templates/`	CMakeLists.txt / app_main.c 模板渲染正确
T8	`core/build_system.py`	编译真实 CubeMX 项目并返回 BuildResult
T9	`core/uart_monitor.py`	串口捕获正常
T10	`cli.py`	init/build/flash/monitor 命令可用
T11	集成测试	真实 F103 板验证端到端闭环

Sprint 2：驱动库 + Review Layer 基础
目标：驱动生成后可自动审查，入库前必须通过规则检查

任务	模块	验收标准
T12	`core/review_engine.py` Layer 2	EMB-001EMB-010 规则可检测违规
T13	`core/review_engine.py` Layer 1	clang-tidy 集成，输出结构化报告
T14	`core/driver_library.py`	驱动 CRUD + SQLite 索引
T15	`tools/generate_driver.py`	基于 JSON 摘要生成驱动
T16	`tools/review_code.py`	独立 review 命令可用
T17	LangGraph	generate → review → fix → store 状态机
T18	集成测试	生成一个违规驱动 → review 检测 → fix 修复 → 通过

Sprint 3：文档理解 + LLM 语义审查
目标：上传 PDF → 提取知识 → 生成驱动 → LLM 语义审查 → 入库

任务	模块	验收标准
T19	`core/pdf_parser.py`	marker/pdfplumber 提取文本+表格
T20	`core/knowledge_base.py`	Chroma 向量数据库存储
T21	`core/review_engine.py` Layer 3	LLM 语义审查集成
T22	`tools/parse_doc.py`	parse-doc 命令可用
T23	集成测试	上传 BMI270.pdf → 生成驱动 → 审查通过 → 编译成功

Sprint 4：闭环调试 + Skill Evolution
目标：编译失败/烧录异常/串口无数据时自动诊断修复，并把成功经验沉淀为协议 skill

任务	模块	验收标准
T24	`core/build_system.py`	GCC 错误解析 + 自动修复
T25	`core/uart_monitor.py`	日志模式分析（卡死/崩溃/数据异常）
T26	LangGraph	debug-loop 状态机（build→flash→monitor→diagnose→fix）
T27	`core/skill_manager.py`	项目成功后自动写入/更新协议 skill
T28	集成测试	BMI270 SPI 驱动通过 review + build 后生成 `spi_generic` skill

Sprint 5：多平台扩展（ESP / Raspberry Pi / VS Code）
目标：工作流不再绑定 CubeMX，支持 ESP-IDF 与 Linux 用户态开发

任务	模块	验收标准
T29	`core/platform_adapter.py`	抽象平台适配接口
T30	`core/platforms/stm32_adapter.py`	STM32 流程接入抽象层
T31	`core/platforms/esp_adapter.py`	ESP-IDF build/flash/monitor 可用
T32	`core/platforms/linux_adapter.py`	Raspberry Pi/Linux 本地构建运行可用
T33	Review Rules	增加 FreeRTOS/Linux 专项审查规则
T34	MCP Server	暴露给 VS Code 插件
T35	VS Code 插件	侧边栏 + 右键菜单 + 状态栏

---

8. 编码红线（强制规范）

红线	说明	违反后果
禁止修改 Core/	Agent 不得修改 CubeMX 生成的 `Core/` 和 `Drivers/` 目录	Review 直接报错
禁止硬编码全局句柄	驱动库禁止引用 `hspi1` 等	EMB-001 Error
禁止 printf/malloc	驱动层禁止标准 I/O 和动态内存	EMB-004/008 Error
必须空指针检查	所有指针参数必须校验	EMB-005 Error
审查不过不能编译	存在 critical/error 级别问题禁止进入 build 阶段	StateGraph 条件阻断
必须自动提交 Git	Agent 每次修改必须 commit，便于回滚	配置默认开启
操作前必须备份	assemble/build 前自动创建快照	BackupManager 强制执行
调试结束必须释放串口	`monitor` / `debug-loop` 必须在成功、超时、异常后关闭串口句柄	串口占用视为实现缺陷
Skill 不得直接从单个项目照搬	必须抽象为协议/平台通用知识	Skill 校验失败
平台特定假设不得泄漏到核心层	ESP/Linux/STM32 差异必须进入 adapter	core review 直接报错

---

9. 附录：快速启动命令

```bash
# 1. 安装依赖
pip install -e .

# 2. 配置（首次运行自动生成默认配置）
agent config --init

# 3. 创建项目
agent init --name BlinkTest --mcu STM32F103C8T6 --platform stm32cubemx --runtime baremetal

# 4. 用户用 CubeMX 打开 projects/BlinkTest/BlinkTest.ioc 配置并生成代码

# 5. Agent 检查配置
agent check-ioc --project BlinkTest

# 6. 组装（自动生成 App/ 代码，自动 review，自动 git commit）
agent assemble --project BlinkTest

# 7. 编译
agent build --project BlinkTest

# 8. 烧录
agent flash --project BlinkTest

# 9. 监控
agent monitor --project BlinkTest --port COM3

# 10. 一键闭环（编译→烧录→监控→诊断→修复）
agent debug-loop --project BlinkTest --probe stlink --port COM3

# 11. 项目成功后沉淀协议 skill
agent update-skill --project BlinkTest --protocol SPI
```

---

这份 v1.2 方案已包含 Review Layer、Git 集成、配置管理、日志审计、并发安全、备份回滚、Prompt 规范、Skill Evolution、多平台扩展骨架 等完整设计，可直接作为实现基线继续拆分开发。


