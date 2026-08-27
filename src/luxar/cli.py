"""LUXAR 命令行入口:统一子命令结构(run / ports / web / setup)。

不带子命令直接运行 `luxar` 等价于 `luxar web`,参数取自环境变量
(LUXAR_PROJECTS_ROOT / LUXAR_SERIAL_PORT / LUXAR_TARGET_CHIP /
LUXAR_WEB_PORT),未设置时使用仓库内 ./projects 等默认值。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from luxar import arguments
from luxar.application.agent_results import (
    agent_exit_code_for_state,
    agent_state_to_result,
    agent_user_message_for_state,
)
from luxar.application.agent_runner import (
    AgentWorkflowProgress,
    resume_agent_workflow,
    run_agent_workflow,
)
from luxar.application.agent_state import AgentState
from luxar.application.results import exit_code_for_state, state_to_result
from luxar.application.runtime_mode import select_firmware_runtime
from luxar.application.runtime_qualification import (
    current_supervisor_qualification,
)
from luxar.application.runtime_metadata_migration import (
    plan_sqlite_runtime_metadata_migration,
)
from luxar.application.runtime_observation import (
    audit_runtime_retirement,
    inspect_sqlite_checkpoint_threads,
)
from luxar.application.runner import WorkflowProgress, run_workflow
from luxar.application.state import WorkflowState
from luxar.bootstrap import (
    build_deepseek_agent_runtime_context,
    build_deepseek_runtime_context,
    discover_serial_ports,
)
from luxar.domain.devices import ApprovalRequest
from luxar.domain.agent.approvals import AgentApprovalRequest
from luxar.domain.interactions import WorkflowInteraction
from luxar.database import (
    LocalStorageRuntime,
    LocalStorageSettings,
)
from luxar.lance_knowledge import LanceDBKnowledgeIndex
from luxar.knowledge import (
    KnowledgeService,
    KnowledgeSettings,
    LocalHashEmbeddingAdapter,
    OpenAIEmbeddingAdapter,
)
from luxar.sdk_knowledge import SdkExampleKnowledgeBase
from luxar.toolchain import EspIdfToolchainManager
from luxar.ports.espidf_errors import EspIdfError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="luxar",
        description="运行 LUXAR ESP-IDF Agent 工作流(不带子命令时启动 Web 网关)",
    )
    subcommands = parser.add_subparsers(dest="command", required=False)
    run_parser = subcommands.add_parser("run", help="运行一个固件任务")
    run_parser.add_argument("--project", type=Path, required=True)
    run_parser.add_argument("--task")
    run_parser.add_argument(
        "--target",
        type=arguments.target_chip,
        help="目标芯片，例如 esp32 或 esp32s3（创建任务时建议提供）",
    )
    run_parser.add_argument(
        "--port",
        type=arguments.serial_port,
        help="开发板串口，例如 COM3（烧录/监控任务必需）",
    )
    run_parser.add_argument(
        "--max-attempts",
        type=arguments.positive_integer,
        default=3,
    )
    run_parser.add_argument(
        "--allow-dependency-downloads",
        action="store_true",
    )
    run_parser.add_argument(
        "--approve-flash",
        action="store_true",
        help="JSON 模式下预授权本次运行的烧录操作",
    )
    run_parser.add_argument("--json", action="store_true")

    ports_parser = subcommands.add_parser("ports", help="列出可用串口设备")

    web_parser = subcommands.add_parser("web", help="启动本地 Web 网关")
    web_parser.add_argument(
        "--projects-root",
        type=Path,
        action="append",
        required=True,
        help="项目根目录，可重复传入多个（页面可切换选择）",
    )
    web_parser.add_argument("--host", default="127.0.0.1")
    web_parser.add_argument(
        "--port",
        type=arguments.positive_integer,
        default=8000,
    )
    web_parser.add_argument(
        "--serial-port",
        type=arguments.serial_port,
        help="开发板串口，例如 COM4（烧录/监控任务需要）",
    )
    web_parser.add_argument(
        "--target",
        type=arguments.target_chip,
        help="目标芯片，例如 esp32（创建任务建议提供）",
    )
    web_parser.add_argument(
        "--max-concurrent-workflows",
        type=arguments.positive_integer,
        default=2,
    )

    setup_parser = subcommands.add_parser(
        "setup",
        help="一键准备开发环境（Windows PowerShell）",
    )
    storage_parser = subcommands.add_parser(
        "storage", help="检查 SQLite + LanceDB 本地持久化"
    )
    storage_commands = storage_parser.add_subparsers(
        dest="storage_command", required=True
    )
    storage_commands.add_parser("health", help="检查本地持久化")
    storage_commands.add_parser(
        "runtime-audit",
        help="只读审计 legacy 回退观察和恢复依赖",
    )
    storage_commands.add_parser(
        "runtime-migration-plan",
        help="只读生成历史 workflow_family 迁移计划",
    )
    return parser


def _report_progress(progress: WorkflowProgress) -> None:
    labels = {
        "requirement": "需求",
        "planning": "计划",
        "build": "构建",
        "flash": "烧录",
        "monitor": "监控",
        "repair": "修复",
        "clarification": "澄清",
        "completed": "完成",
        "failed": "失败",
    }
    print(
        f"[{labels[progress.stage]}] {progress.message}",
        file=sys.stderr,
    )


def _exit_code_for_state(state: WorkflowState) -> int:
    """保留原有私有函数名，实际规则由共享展示合同提供。"""

    return exit_code_for_state(state)


def _state_to_json_envelope(state: WorkflowState) -> dict[str, object]:
    """保留 CLI 内部入口，避免 CLI 与 Web 各维护一份白名单。"""

    return state_to_result(state)


def _print_approval_request(
    request: ApprovalRequest | WorkflowInteraction | AgentApprovalRequest,
) -> None:
    # 只展示审批请求里的受控字段。
    if isinstance(request, AgentApprovalRequest):
        print(f"—— {request.title} ——", file=sys.stderr)
        print(request.summary, file=sys.stderr)
        print(f"操作：{request.operation}", file=sys.stderr)
        if request.task_description:
            print(f"任务内容：{request.task_description}", file=sys.stderr)
        sections = (
            ("批准后将执行", request.planned_actions),
            ("调用工具", request.tools),
            ("影响范围", request.affected_targets),
            ("验收条件", request.acceptance_criteria),
            ("必须保持", request.preserve_conditions),
            ("主要风险", request.risks),
        )
        for heading, values in sections:
            if not values:
                continue
            print(f"{heading}：", file=sys.stderr)
            for index, value in enumerate(values, 1):
                print(f"  {index}. {value}", file=sys.stderr)
        return
    if isinstance(request, WorkflowInteraction):
        print(f"—— {request.title} ——", file=sys.stderr)
        print(request.summary, file=sys.stderr)
        if request.plan is not None:
            for index, step in enumerate(request.plan.steps, 1):
                print(f"{index}. {step.kind}：{step.description}", file=sys.stderr)
        for question in request.questions:
            print(f"需要确认：{question}", file=sys.stderr)
        return
    print("—— 烧录审批 ——", file=sys.stderr)
    print(f"项目：{request.project_name}", file=sys.stderr)
    print(f"串口：{request.port}", file=sys.stderr)
    if request.target_chip:
        print(f"芯片：{request.target_chip}", file=sys.stderr)
    print(f"说明：{request.summary}", file=sys.stderr)


def _interactive_approval(
    request: ApprovalRequest | WorkflowInteraction | AgentApprovalRequest,
) -> bool:
    _print_approval_request(request)
    try:
        prompt = (
            "批准烧录？(y/N)："
            if isinstance(request, ApprovalRequest)
            else "批准并继续？(y/N)："
        )
        answer = input(prompt).strip().casefold()
    except EOFError:
        return False
    return answer in {"y", "yes", "是"}


def _run_ports() -> int:
    try:
        ports = discover_serial_ports()
    except EspIdfError as error:
        print(error.message, file=sys.stderr)
        return 1

    if not ports:
        print("未发现可用串口设备", file=sys.stderr)
        return 0

    for port in ports:
        print(port.name)
        if port.description:
            print(f"  {port.description}")
        if port.hardware_id:
            print(f"  {port.hardware_id}")
    return 0


def _run_web(args: argparse.Namespace) -> int:
    # 延迟导入:不启动网关的 CLI 命令无需加载 FastAPI。
    from luxar.web import serve

    return serve(
        projects_roots=args.projects_root,
        host=args.host,
        port=args.port,
        serial_port=args.serial_port,
        target_chip=args.target,
        max_concurrent_workflows=args.max_concurrent_workflows,
    )


def _run_setup() -> int:
    # 复用仓库自带的 PowerShell 准备脚本,保证与脚本方式行为一致。
    import os
    import subprocess

    if os.name != "nt":
        print("setup 目前仅支持 Windows PowerShell", file=sys.stderr)
        return 2

    script = (
        Path(__file__).resolve().parents[2] / "scripts" / "setup.ps1"
    )
    if not script.is_file():
        print("找不到 scripts/setup.ps1", file=sys.stderr)
        return 2

    return subprocess.call(
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    )


def _report_agent_progress(progress: AgentWorkflowProgress) -> None:
    print(f"[Supervisor] {progress.message}", file=sys.stderr)


def _run_storage(command: str) -> int:
    if command not in {
        "health",
        "runtime-audit",
        "runtime-migration-plan",
    }:
        return 2
    runtime = LocalStorageRuntime(
        LocalStorageSettings.for_projects_root(_default_projects_roots()[0])
    )
    try:
        runtime.open()
        if command == "runtime-audit":
            report = audit_runtime_retirement(
                runtime.persistence,
                checkpoint_thread_ids=inspect_sqlite_checkpoint_threads(
                    runtime.settings.checkpoint_path
                ),
            )
            print(
                json.dumps(
                    report.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if command == "runtime-migration-plan":
            plan = plan_sqlite_runtime_metadata_migration(
                runtime.settings.application_path
            )
            print(
                json.dumps(
                    plan.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        runtime.checkpointer()
        if runtime.health():
            print(f"SQLite 持久化正常：{runtime.settings.application_path}")
            print(f"LanceDB 目录：{runtime.settings.knowledge_path}")
            return 0
    except (RuntimeError, ValueError, OSError, sqlite3.Error):
        print("本地持久化不可用，请检查 LUXAR_STORAGE_DIRECTORY", file=sys.stderr)
        return 1
    finally:
        runtime.close()
    print("本地持久化健康检查失败", file=sys.stderr)
    return 1


def _format_human_result(state: WorkflowState) -> str:
    status = state.get("status")
    lines: list[str]

    if status == "completed":
        lines = [
            "LUXAR 执行成功",
            "状态：completed",
            f"构建次数：{state.get('attempts', 0)}",
        ]
        changed_files = state.get("changed_files", [])
        if changed_files:
            lines.append("修改文件：")
            lines.extend(f"  - {path}" for path in changed_files)
        evidence = state.get("build_evidence")
        if evidence is not None:
            lines.append(f"最终命令：{' '.join(evidence.command)}")
            lines.append(f"返回码：{evidence.return_code}")
        return "\n".join(lines)

    if status == "needs_clarification":
        lines = ["LUXAR 需要更多信息", "状态：needs_clarification"]
        requirement = state.get("requirement")
        missing_fields = (
            requirement.blocking_missing_fields
            if requirement is not None
            else []
        )
        if missing_fields:
            lines.append("缺少字段：")
            lines.extend(f"  - {field}" for field in missing_fields)
        return "\n".join(lines)

    lines = ["LUXAR 执行失败", "状态：failed"]
    error = state.get("error")
    if error is not None:
        lines.extend(
            [
                f"阶段：{error.stage}",
                f"类别：{error.category}",
                f"原因：{error.message}",
            ]
        )
        if error.user_suggestion:
            lines.append(f"建议：{error.user_suggestion}")
    return "\n".join(lines)


def _print_result(state: WorkflowState, json_mode: bool) -> None:
    """只输出已经通过应用和 Adapter 边界的最终业务结果。"""

    if json_mode:
        print(
            json.dumps(
                _state_to_json_envelope(state),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        print(_format_human_result(state))


def _print_agent_result(state: AgentState, json_mode: bool) -> None:
    if json_mode:
        print(
            json.dumps(
                agent_state_to_result(state),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        print(agent_user_message_for_state(state))


_JSON_APPROVAL_ERROR = (
    "JSON 模式无法交互审批，请使用 --approve-flash 预授权或改用交互模式"
)


def _default_projects_roots() -> list[Path]:
    # 多根目录用路径分隔符拼接(LUXAR_PROJECTS_ROOT=根1;根2)。
    raw = os.environ.get("LUXAR_PROJECTS_ROOT")
    if raw:
        return [
            Path(part.strip())
            for part in raw.split(os.pathsep)
            if part.strip()
        ]

    return [Path("projects")]


def _default_web_port() -> int:
    raw = os.environ.get("LUXAR_WEB_PORT", "8000")
    try:
        return arguments.positive_integer(raw)
    except argparse.ArgumentTypeError:
        return 8000


def _load_env_file() -> None:
    """把仓库 .env 的 KEY=VALUE 加载为环境变量(不覆盖已有值)。

    依次检查当前目录与仓库根目录;真实环境变量优先级最高,
    setup.ps1/run-web.ps1 先设置的值也不会被覆盖。
    LUXAR_SKIP_DOTENV=1 时完全跳过(测试隔离用)。
    """

    if os.environ.get("LUXAR_SKIP_DOTENV"):
        return

    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    seen: set[Path] = set()

    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)

        for line in candidate.read_text(
            encoding="utf-8-sig"
        ).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


def main(argv: Sequence[str] | None = None) -> int:
    _load_env_file()
    args = build_parser().parse_args(argv)

    if args.command is None:
        # 裸 luxar = luxar web,配置来自环境变量或仓库默认值。
        roots = _default_projects_roots()
        for root in roots:
            if not root.exists():
                root.mkdir(parents=True, exist_ok=True)

        args = argparse.Namespace(
            command="web",
            projects_root=roots,
            host="127.0.0.1",
            port=_default_web_port(),
            serial_port=os.environ.get("LUXAR_SERIAL_PORT") or None,
            target=os.environ.get("LUXAR_TARGET_CHIP") or None,
            max_concurrent_workflows=2,
        )

    if args.command == "ports":
        return _run_ports()

    if args.command == "web":
        return _run_web(args)

    if args.command == "setup":
        return _run_setup()

    if args.command == "storage":
        return _run_storage(args.storage_command)

    project: Path = args.project

    # 创建任务允许项目目录尚不存在，但父目录必须真实存在。
    parent = project.parent
    if not parent.exists() or not parent.is_dir():
        print("项目父目录不存在或不是目录", file=sys.stderr)
        return 2

    if project.exists() and not project.is_dir():
        print("项目路径已存在但不是目录", file=sys.stderr)
        return 2

    if args.json and args.task is None:
        print("JSON 模式必须提供 --task", file=sys.stderr)
        return 2

    if args.approve_flash and not args.json:
        print("--approve-flash 只能与 --json 一起使用", file=sys.stderr)
        return 2

    storage_runtime: LocalStorageRuntime | None = None
    active_run_thread_id: str | None = None
    run_persistence: object | None = None
    try:
        task = args.task if args.task is not None else input("请输入固件需求：")
        task = task.strip()
        if not task:
            print("固件需求不能为空", file=sys.stderr)
            return 2
        runtime_selection = select_firmware_runtime(
            qualification=current_supervisor_qualification()
        )
        runtime_mode = runtime_selection.mode
        try:
            storage_runtime = LocalStorageRuntime(
                LocalStorageSettings.for_projects_root(project.parent)
            )
            storage_runtime.open()
            persistence = storage_runtime.persistence
            checkpointer = storage_runtime.checkpointer()
            knowledge_service = None
            embedding_settings = KnowledgeSettings()
            if embedding_settings.configured:
                knowledge_index = LanceDBKnowledgeIndex(
                    storage_runtime.settings.knowledge_path,
                    dimensions=embedding_settings.dimensions,
                )
                knowledge_service = KnowledgeService(
                    knowledge_index,
                    OpenAIEmbeddingAdapter(embedding_settings),
                )
            sdk_embeddings = LocalHashEmbeddingAdapter()
            sdk_example_knowledge = SdkExampleKnowledgeBase(
                LanceDBKnowledgeIndex(
                    storage_runtime.settings.sdk_knowledge_path,
                    dimensions=sdk_embeddings.dimensions,
                ),
                sdk_embeddings,
            )
            toolchain_manager = EspIdfToolchainManager(
                config_path=project.parent / ".luxar" / "toolchain.json",
            )
            resolved_idf_command = toolchain_manager.command or ("idf.py",)
            resolved_idf_path = (
                Path(toolchain_manager.status.idf_path)
                if toolchain_manager.status.idf_path
                else None
            )
            if runtime_mode == "supervisor":
                if not project.exists():
                    project.mkdir()
                context = build_deepseek_agent_runtime_context(
                    project_path=project,
                    serial_port=args.port,
                    allow_dependency_downloads=args.allow_dependency_downloads,
                    idf_command=resolved_idf_command,
                )
            else:
                bootstrap_options: dict[str, object] = {
                    "project_path": project,
                    "target_chip": args.target,
                    "serial_port": args.port,
                    "allow_dependency_downloads": args.allow_dependency_downloads,
                }
                if resolved_idf_path is not None:
                    bootstrap_options["idf_path"] = resolved_idf_path
                bootstrap_options["idf_command"] = resolved_idf_command
                bootstrap_options.update(
                    {
                        "checkpointer": checkpointer,
                        "persistence": persistence,
                        "project_key": project.name,
                        "knowledge_service": knowledge_service,
                        "sdk_example_knowledge": sdk_example_knowledge,
                    }
                )
                context = build_deepseek_runtime_context(**bootstrap_options)
        except (ValidationError, ValueError, RuntimeError, OSError):
            print("运行配置无效，请检查环境变量", file=sys.stderr)
            return 2

        run_thread_id = uuid.uuid4().hex
        persistence.start_run(
            thread_id=run_thread_id,
            task_key=project.name,
            project_name=project.name,
            root_index=0,
            task_text=task,
            runtime_config={
                "project_name": project.name,
                "serial_port": args.port,
                "target_chip": args.target,
                "allow_dependency_downloads": args.allow_dependency_downloads,
                "agent_runtime": runtime_mode,
                "firmware_runtime": runtime_mode,
                "firmware_runtime_reason": runtime_selection.reason,
                "workflow_family": (
                    "supervisor_firmware"
                    if runtime_mode == "supervisor"
                    else "legacy_firmware_rollback"
                ),
                "entrypoint": "cli",
            },
        )
        active_run_thread_id = run_thread_id
        run_persistence = persistence

        if runtime_mode == "supervisor":
            run_result = run_agent_workflow(
                initial_state={
                    "task_text": task,
                    "source_message_id": uuid.uuid4().hex,
                    "project_name": project.name,
                    "target_chip": args.target,
                    "trace": [],
                    "max_steps": max(20, args.max_attempts * 10),
                },
                context=context,  # type: ignore[arg-type]
                checkpointer=checkpointer,
                persistence=persistence,
                project_key=project.name,
                thread_id=run_thread_id,
                progress_reporter=(
                    None if args.json else _report_agent_progress
                ),
            )
            while run_result.pending_approval is not None:
                if args.json:
                    if (
                        not args.approve_flash
                        or run_result.pending_approval.operation
                        != "device.flash"
                    ):
                        envelope = agent_state_to_result(run_result.state)
                        persistence.finish_run(
                            run_thread_id,
                            status="failed",
                            result=envelope,
                        )
                        active_run_thread_id = None
                        print(_JSON_APPROVAL_ERROR, file=sys.stderr)
                        return 4
                    approved = True
                else:
                    approved = _interactive_approval(
                        run_result.pending_approval
                    )
                if run_result.checkpointer is None:
                    persistence.finish_run(
                        run_thread_id,
                        status="failed",
                        result=agent_state_to_result(run_result.state),
                    )
                    active_run_thread_id = None
                    print("Supervisor 审批恢复状态不可用", file=sys.stderr)
                    return 4
                run_result = resume_agent_workflow(
                    thread_id=run_result.thread_id,
                    context=context,  # type: ignore[arg-type]
                    checkpointer=run_result.checkpointer,
                    approved=approved,
                    persistence=persistence,
                    project_key=project.name,
                    progress_reporter=_report_agent_progress,
                )
            agent_state = run_result.state
            agent_envelope = agent_state_to_result(agent_state)
            persistence.finish_run(
                run_thread_id,
                status=str(agent_envelope["status"]),
                result=agent_envelope,
            )
            active_run_thread_id = None
            _print_agent_result(agent_state, args.json)
            return agent_exit_code_for_state(agent_state)

        initial_state = WorkflowState(
            task_text=task,
            attempts=0,
            max_attempts=args.max_attempts,
            trace=[],
        )
        # 交互模式即时决策；JSON 模式显式预授权或让工作流暂停。
        approval_handler: object | None = None
        if not args.json:
            approval_handler = _interactive_approval
        elif args.approve_flash:
            approval_handler = lambda request: True

        run_result = run_workflow(
            initial_state=initial_state,
            context=context,
            progress_reporter=None if args.json else _report_progress,
            approval_handler=approval_handler,  # type: ignore[arg-type]
            thread_id=run_thread_id,
        )
        if run_result.pending_approval is not None:
            # JSON 模式未预授权：工作流已在审批前暂停，只能终止。
            persistence.finish_run(
                run_thread_id,
                status="failed",
                result=state_to_result(run_result.state),
            )
            active_run_thread_id = None
            print(_JSON_APPROVAL_ERROR, file=sys.stderr)
            return 4

        result = run_result.state
        result_envelope = state_to_result(result)
        persistence.finish_run(
            run_thread_id,
            status=str(result_envelope["status"]),
            result=result_envelope,
        )
        active_run_thread_id = None
    except KeyboardInterrupt:
        if active_run_thread_id is not None and run_persistence is not None:
            run_persistence.finish_run(  # type: ignore[attr-defined]
                active_run_thread_id,
                status="failed",
                result={
                    "status": "failed",
                    "message": "操作已取消",
                    "exit_code": 130,
                },
            )
        print("操作已取消", file=sys.stderr)
        return 130
    finally:
        if storage_runtime is not None:
            storage_runtime.close()

    _print_result(result, args.json)
    return _exit_code_for_state(result)


if __name__ == "__main__":
    raise SystemExit(main())
