# LUXAR 证据驱动源码修复闭环设计

**日期：** 2026-08-01

**状态：** 已由学习者确认

**适用仓库：** `C:\tmp\luxar-langgraph`

## 1. 修订原因

原纵向切片把可重试构建失败直接路由回 `build_project`。该行为只适用于可能自行消失的超时；源码或链接错误在文件未变化时重复运行相同命令没有修复价值。

本设计增加一条真实 Agent 修复链路：模型根据结构化构建证据和项目文件生成结构化修复计划，受限 Workspace Adapter 自动应用完整文件替换，再由 ESP-IDF 构建产生新的真实证据。

本文档补充并修订 `2026-08-01-luxar-langgraph-enterprise-design.md`。发生冲突时，以本文档的修复闭环、接口和路由规则为准。

## 2. 最终业务拓扑

```text
START
  ↓
analyze_requirement
  ├─ requirement incomplete → request_clarification → END
  └─ requirement complete
         ↓
    create_plan
         ↓
    build_project
         ↓
    BuildEvidence + BuildDiagnostic[]
         ├─ success → completed → END
         ├─ timeout and budget remains → build_project
         ├─ source/linker and budget remains → repair_project
         │                                      ├─ read project files
         │                                      ├─ create RepairPlan
         │                                      └─ apply replacements
         │                                               ↓
         └──────────────────────────────────────── build_project
         └─ environment/unknown/budget exhausted → failed → END
```

“重试”与“修复”是不同动作：超时可以原样重试；源码和链接错误必须先改变项目文件；环境错误和未知错误在本阶段直接终止。

## 3. 构建诊断

`BuildEvidence` 保留工具事实，并新增 `diagnostics: list[BuildDiagnostic]`。`BuildDiagnostic` 包含：

- `file: str | None`：编译器报告的文件路径；真实 Adapter 尽可能规范化为项目相对路径。
- `line: int | None`：一基行号。
- `column: int | None`：一基列号。
- `severity: Literal["warning", "error"]`。
- `code: str | None`：可获得时保存编译器诊断码。
- `message: str`：具体诊断信息。

`stderr_summary` 继续保留，因为并非所有 GCC、CMake 和链接器上下文都能完整结构化。只有 `EspIdfPort` 的 Adapter 能创建构建证据；DeepSeek 不得声称构建成功。

## 4. 修复领域对象

新增 `src/luxar/domain/repairs.py`：

- `ProjectFile(path, content)`：提供给修复模型的项目文件快照。
- `FileReplacement(path, content)`：模型提出的完整文件替换。
- `RepairPlan(diagnosis, replacements)`：一次结构化修复决定。

`ProjectFile.path` 和 `FileReplacement.path` 必须是规范化项目相对路径。拒绝空路径、绝对路径、Windows 盘符路径以及包含 `..` 的路径。`RepairPlan.replacements` 至少包含一个文件，并拒绝重复目标路径。

第一版采用完整文件内容，不采用搜索替换块或 unified diff。这降低了解析和应用复杂度；以后可替换修复表示法而不改变 Graph 的业务拓扑。

## 5. Ports 与 Adapters

新增两个应用所有的能力接口：

```python
class RepairPlanner(Protocol):
    def create_repair(
        self,
        requirement: FirmwareRequirement,
        plan: ExecutionPlan,
        evidence: BuildEvidence,
        files: list[ProjectFile],
    ) -> RepairPlan: ...
```

```python
class WorkspacePort(Protocol):
    def read_project_files(
        self,
        project_path: Path,
    ) -> list[ProjectFile]: ...

    def apply_repair(
        self,
        project_path: Path,
        repair: RepairPlan,
    ) -> list[str]: ...
```

`RepairPlanner` 负责提出修复，不访问磁盘。第一阶段使用 `FakeRepairPlanner`，真实阶段使用 `DeepSeekRepairPlanner`。

`WorkspacePort` 负责受控读写，不进行 LLM 推理。第一阶段使用记录调用的 `FakeWorkspace`，真实阶段使用 `LocalWorkspaceAdapter`。

真实 Workspace Adapter 必须再次解析每个目标路径，并证明解析后的路径仍位于 `project_path` 内。领域校验是第一层，写入时边界校验是第二层。允许读取的文件类型、文件数量和总字节数必须配置上限；密钥、Git 元数据、构建产物和项目目录外文件不得进入模型上下文。

## 6. `repair_project` 节点

Graph 只增加一个业务节点 `repair_project`。它从 State 取得 `FirmwareRequirement`、`ExecutionPlan` 和最后一次 `BuildEvidence`，从 Runtime Context 取得 `RepairPlanner`、`WorkspacePort` 和 `project_path`。

节点依次：读取项目快照、创建 `RepairPlan`、应用完整文件替换，并向 State 写入 `repair_plan`、`changed_files`、`status="repaired"` 和 trace。它不增加 `attempts`；构建次数只由下一次 `build_project` 增加。

读取文件、调用模型和写文件是 `repair_project` 的内部能力调用，不分别建成 LangGraph 节点。Graph 表达业务阶段，Ports 隔离技术细节。

## 7. State 与 Runtime Context 修订

`WorkflowState` 新增：

```python
repair_plan: RepairPlan
changed_files: list[str]
```

`WorkflowStatus` 新增 `"repaired"`。

`RuntimeContext` 新增：

```python
repair_planner: RepairPlanner
workspace: WorkspacePort
```

Adapter 实例、客户端、API key 和项目绝对路径仍不进入持久化 State。

## 8. 路由规则

`route_after_build` 是纯条件路由，返回以下目的地之一：

```python
Literal["completed", "repair_project", "build_project", "failed"]
```

判断顺序固定为：

1. `evidence.success` 为真：`completed`。
2. `attempts >= max_attempts`：`failed`。
3. `error_category in {"source", "linker"}`：`repair_project`。
4. `error_category == "timeout"`：`build_project`。
5. `environment`、`unknown` 或缺少类别：`failed`。

最后一次 `BuildEvidence` 在修复和终止路径中均保留。Graph recursion limit 只是兜底，业务终止由 `attempts` 与 `max_attempts` 证明。

## 9. 自动应用与安全边界

第一版不加入人工审批，验证通过的 `RepairPlan` 由 `WorkspacePort` 自动应用。自动应用不代表模型拥有任意文件系统权限：

- 模型只输出 Domain 对象，不获得文件工具句柄。
- 所有写入目标必须是项目内相对路径。
- Workspace Adapter 使用固定项目根目录和允许文件规则。
- 一次 RepairPlan 的文件数量和总内容大小有上限。
- 应用结果以 `changed_files` 记录，但不能作为构建成功证据。
- 修改后必须重新调用 `EspIdfPort.build()`。

人工批准将在后续独立切片通过 LangGraph `interrupt()` 和 checkpointer 加入。

## 10. 测试与完成标准

第一阶段继续使用 Fake Adapters，但必须验证真实控制流：

- 结构化诊断保存文件、行、列和消息。
- 非法替换路径与重复目标被 Domain 拒绝。
- `repair_project` 按顺序调用 Workspace、RepairPlanner、Workspace，并保存结果。
- 成功构建直接完成。
- timeout 在预算内直接重建，不调用修复能力。
- source/linker 在预算内先修复再重建。
- environment、unknown 和次数耗尽直接失败。
- “源码失败 → 修复 → 构建成功”的集成测试证明修复调用一次、构建调用两次，并保留最终成功证据。
- 替换 Fake 为 DeepSeek 和本地 Workspace Adapter 不修改 Graph 拓扑。

