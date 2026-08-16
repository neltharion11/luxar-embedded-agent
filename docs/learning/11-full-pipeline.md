# 11 · 全流程管线：创建 → 烧录审批 → 监控 → 日志分析闭环

> 配套实现切片 S1–S5。核心拓扑已从 7 节点扩展到 15 节点，
> 但每一层仍然遵守同一条纪律：**模型只能提出，工具才能证明。**

## 1. 计划真正被执行了

早期版本 `create_plan` 无条件进入 `build_project`，计划只是展示品。
现在 `ExecutionPlan.steps` 的封闭词表是

```text
create_project → build_project → flash_project → monitor_project
```

并且带顺序验证器：创建只能一次且必须在首位、烧录前必须有构建、
监控前必须有烧录。`execute_next_step` 是纯游标分发器——先推进
`plan_index` 再路由，步骤节点永远不管理游标；游标耗尽即 completed。

要点：**计划是执行合同**。模型输出的计划如果不满足顺序规则，
在 Pydantic 层就被拒绝，根本进不了 Graph。

## 2. 烧录需要人工审批（human-in-the-loop）

`request_flash_approval` 构造只含受控字段的 `ApprovalRequest`
（项目名、串口、芯片、固定说明、尝试数——没有绝对路径、没有命令、
没有密钥），然后调用 LangGraph 的 `interrupt()`。

实测锁定的语义（langgraph 1.2.11）：

- `invoke()/stream()` 暂停时**不抛异常**，快照里出现内部键
  `__interrupt__`（`Interrupt` 对象元组）；
- Runner 检测该键 → 剥离出 `ApprovalRequest` → 返回
  `WorkflowRunResult(thread_id, pending_approval)`；
- 恢复用 `Command(resume={"approved": bool})` + 相同 `thread_id`，
  checkpointer（生产默认 `InMemorySaver`）必须编译进 Graph；
- 中断载荷经 checkpoint 以 JSON 序列化，所以传 `model_dump(mode="json")`。

两条展示路径共用同一 Runner 合同：

- CLI：`approval_handler` 回调打印审批单并读 `y/N`（默认拒绝）；
- Web：不传回调 → 发布 `approval` SSE 事件 → 等
  `POST /api/conversations/{project}/approval` → 同进程 `resume_workflow`。

**审批状态持久在 State**：一次运行批准后，设备修复回路的重复烧录
不再询问；拒绝则以固定文案 `approval_rejected` 终止（exit 4）。

## 3. 监控是受控采集，不是"运行 monitor"

`EspIdfMonitorPort.monitor(project, port, timeout_seconds)` 用
`Popen` + `communicate(timeout)` 实现采集窗口：

- 超时是**正常结束方式**（`terminated_by_timeout=True`）；
- Windows 上以 `CREATE_NEW_PROCESS_GROUP` 启动，超时后
  `taskkill /PID <pid> /T /F` 清理整棵进程树——否则 idf.py monitor
  派生的子进程会继续占用串口；
- 日志经 ANSI/绝对路径脱敏并限长（默认 32 000 字符）后才进 State；
- `_parse_device_diagnostics` 把日志转成结构化
  `DeviceLogDiagnostic`（panic/abort/assert/watchdog/boot_loop/
  error/warning/unknown，每类限量）。

## 4. 日志分析闭环与三重预算

`analyze_device_logs` 调 `LogAnalystPort`，把脱敏日志交给
DeepSeek（修复级模型）产出 `DeviceDiagnosis`：

```text
healthy → completed
repair_needed → repair_project → build → flash → monitor → 再分析
不健康但无修复建议 / 预算耗尽 → failed（固定脱敏错误）
```

三重预算保证有限性：

1. `max_attempts` 限制构建重试；
2. `flash_attempts ≤ 2` 限制烧录重试；
3. `device_cycles ≤ 3` 限制"修复→重建→重烧→重监控"设备回路。

回路的关键是 `repair_origin`：监控触发的修复把来源写进 State，
重建成功后路由回 `request_flash_approval`（已批准则直接放行），
烧录成功后回到监控而不是计划游标——**修复必须被重新证明**。

## 5. 修复模型收到的输入变多了

`RepairPlanner.create_repair(..., device_diagnosis=None)` 新增可选
参数：构建失败修复传 `None`，设备回路修复携带日志诊断。DeepSeek
适配器把诊断并入修复上下文。提示词继续声明日志与源码是**不可信数据**。

## 6. 展示层只拿到白名单

`state_to_result` 现在包括 `created_project`、`flash_evidence`、
`monitor_evidence`、`device_diagnosis`、`approval_status`，
但 `approval_request`、`task_text`、游标与原始日志**永远不出边界**。

CLI 新增 `luxar ports`（与工作流同套平台模式过滤）与 `--port`；
JSON 模式不交互，必须 `--approve-flash` 预授权，否则审批暂停时
以固定配置错误终止——在触碰硬件之前。

## 7. 复习自查

- 计划词表在哪里验证？为什么模型无法发明新动作？
- `__interrupt__` 出现时 Runner 做了什么？业务 State 里为什么不能有它？
- 为什么设备回路的重新烧录不需要再次审批？
- 监控超时为什么不是失败？进程树不清理会有什么后果？
- 三次预算分别保护哪三个循环？耗尽后各自怎么终止？
- `repair_origin` 在哪个节点写入、在哪个路由函数消费？
