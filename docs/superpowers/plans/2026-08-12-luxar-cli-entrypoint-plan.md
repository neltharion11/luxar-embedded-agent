# LUXAR CLI Entrypoint Implementation Plan

**Goal:** 为现有七节点 LUXAR 工作流增加可安装的 `luxar run` 命令，支持普通交互模式、安全进度、稳定 JSON 和明确退出码。

**Architecture:** CLI 只做参数、交互和展示；Bootstrap 继续装配真实能力；Runner 继续作为唯一 Graph 执行与能力异常边界，并新增不包含 State 的安全进度回调。CLI 不直接调用 Graph，也不接触 SDK、subprocess、YAML 或工作区内部实现。

**Authoritative spec:** `docs/superpowers/specs/2026-08-12-luxar-cli-entrypoint-design.md`

**Test command:**

```text
C:\Users\Gugugu\.conda\envs\luxar-learning\python.exe -m pytest -v -p no:cacheprovider
```

**Working rules:** Codex 完成已批准范围内的生产代码、测试和 Markdown 后再集中教学；不修改、不暂存、不提交 `.vscode/`；七节点拓扑和现有 Port 签名保持不变。

## Task 1：为 Runner 增加安全进度事件

**Files:**

- Modify: `src/luxar/application/runner.py`
- Modify: `tests/application/test_runner.py`

- [x] 写失败测试：完整修复链按节点顺序产生固定 `WorkflowProgress`，对象只含 `stage/message/attempts`。
- [x] 写失败测试：命令前 Port 异常只产生一次 failed 事件，并保留原有失败 State。
- [x] 写失败测试：未传 reporter 时行为不变；reporter 自身异常向外传播，不能被转换为 `WorkflowError`。
- [x] 实现冻结的 `WorkflowProgress`、`ProgressReporter`、固定节点映射和可选 Runner 参数。
- [x] 用显式迭代器只在 `next(graph_stream)` 周围捕获三类能力异常；在捕获区外调用 reporter，防止 reporter 抛出的同名异常被误当成工具失败。
- [x] 根据 trace 长度去重，忽略无新 trace 的快照；attempts 只复制整数。
- [x] 运行 Runner 聚焦测试并提交：`feat: report safe workflow progress`。

## Task 2：实现 CLI 参数、交互和装配

**Files:**

- Create: `src/luxar/cli.py`
- Create: `tests/test_cli.py`
- Modify: `pyproject.toml`

- [x] 写参数失败测试：缺少 `run`/`--project`、未知参数、非正 max attempts 由标准 argparse 产生 `SystemExit(2)`。
- [x] 写应用级输入测试：路径不存在/不是目录、空 task、JSON 缺 task 返回 `2`；JSON 缺 task 不调用 `input()`。
- [x] 写交互测试：普通模式缺 task 时调用 `input("请输入固件需求：")` 并去除首尾空白。
- [x] 写装配测试：默认和显式下载授权、项目 Path、初始 State、max attempts 正确传给 Bootstrap/Runner。
- [x] 实现 `build_parser()`、正整数解析器和 `main(argv: Sequence[str] | None = None) -> int`。
- [x] 只把已知 Pydantic `ValidationError` 与组合阶段 `ValueError` 转成固定启动配置错误；不输出原异常，不捕获宽泛 `Exception`。
- [x] 在交互、Bootstrap、Runner 周围处理 `KeyboardInterrupt`，固定 stderr 并返回 `130`。
- [x] 在 `pyproject.toml` 注册 `luxar = "luxar.cli:main"`，重新 editable install 后验证 `luxar --help`。
- [x] 运行 CLI 聚焦测试并提交：`feat: add LUXAR command-line entrypoint`。

## Task 3：实现安全普通输出和稳定 JSON

**Files:**

- Modify: `src/luxar/cli.py`
- Modify: `tests/test_cli.py`

- [x] 写 completed/needs_clarification/failed 三种退出码与中文摘要失败测试。
- [x] 写 stderr 进度测试：普通模式按安全事件输出，stdout 只含最终摘要。
- [x] 写 JSON 外壳测试：stdout 恰好一份可解析 JSON，缺失对象为 null，list 字段默认空列表，不含 task/project/context/API key。
- [x] 写 JSON failed 测试：业务结果仍写 stdout 且进程返回 `4`；JSON 模式不安装 reporter。
- [x] 实现纯函数 `_exit_code_for_state`、`_state_to_json_envelope`、`_format_human_result`、`_format_progress`。
- [x] Pydantic 对象只通过 `model_dump(mode="json")` 序列化；仅 Evidence 中已经脱敏和限长的摘要可进入 JSON。
- [x] 运行 CLI 完整测试并提交：`feat: format CLI progress and results`。

## Task 4：端到端安装验证与边界审计

**Files:**

- Modify: `tests/test_cli.py` or create a focused installed-entrypoint test only if subprocess installation verification cannot remain deterministic
- Modify: `README.md`

- [x] 运行 editable install，确认生成 `luxar` 命令。
- [x] 用 `luxar --help` 和 `luxar run --help` 验证 console script，不调用 DeepSeek/ESP-IDF。
- [x] 运行完整 pytest，记录真实 collected/pass/skip 数字。
- [x] 搜索确认 CLI 不导入 `build_graph`、`subprocess`、`yaml`、OpenAI SDK；没有 API-key CLI 参数；下载授权只从 flag 进入 Bootstrap。
- [x] 确认 Graph 仍为七节点，现有 Runner 能力异常只有一个边界。
- [x] 确认 JSON 测试中 stdout 没有进度，普通模式进度只在 stderr。

## Task 5：同步教学文档并最终提交

**Files:**

- Create: `docs/learning/10-cli-entrypoint.md`
- Modify: `docs/learning/00-LUXAR-Agent-复习总览.md`
- Modify: `docs/learning/PROGRESS.md`
- Modify: `README.md`
- Modify: this plan

- [x] 生成中文第 10 章：CLI/shell/argv/parser/subcommand/flag/option/stdin/stdout/stderr/exit code/callback/serialization/presentation adapter 对照。
- [x] 解释 `pyproject.toml` 如何把 `luxar` 映射到 `luxar.cli:main`。
- [x] 解释参数怎样显式变为初始 State，为什么 progress 不是 State 快照。
- [x] 解释普通模式和 JSON 自动化模式、argparse 测试真正执行了什么。
- [x] 同步 README、总览、PROGRESS 的真实结果和下一切片；不预填测试数字。
- [x] 重新运行规定完整测试、`git diff --check`、安全搜索和 `git status --short`。
- [x] 勾选本计划全部完成项并提交：`docs: complete LUXAR CLI lesson`。

## Final Gate

1. `luxar run` 可通过 editable installation 使用。
2. `--project` 始终显式，普通模式才允许交互 task。
3. JSON 模式永不交互、无进度污染、只有一份最终 JSON。
4. 下载授权只来自显式 flag，密钥只来自环境变量。
5. Runner 是唯一 Graph 执行和能力错误边界；reporter 异常不会被伪装成业务失败。
6. 普通输出、JSON 和退出码严格符合规格。
7. Graph、Domain、Ports 和现有 Adapter 安全边界不变。
8. 完整测试、安全审计和 Git 检查均有新鲜成功证据；`.vscode/` 未进入 Git。
