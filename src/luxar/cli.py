"""LUXAR 命令行入口:统一子命令结构(run / ports / web / setup)。"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from luxar import arguments
from luxar.application.results import exit_code_for_state, state_to_result
from luxar.application.runner import WorkflowProgress, run_workflow
from luxar.application.state import WorkflowState
from luxar.bootstrap import build_deepseek_runtime_context, discover_serial_ports
from luxar.domain.devices import ApprovalRequest
from luxar.ports.espidf_errors import EspIdfError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="luxar",
        description="运行 LUXAR ESP-IDF Agent 工作流",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
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
    web_parser.add_argument("--projects-root", type=Path, required=True)
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


def _print_approval_request(request: ApprovalRequest) -> None:
    # 只展示审批请求里的受控字段。
    print("—— 烧录审批 ——", file=sys.stderr)
    print(f"项目：{request.project_name}", file=sys.stderr)
    print(f"串口：{request.port}", file=sys.stderr)
    if request.target_chip:
        print(f"芯片：{request.target_chip}", file=sys.stderr)
    print(f"说明：{request.summary}", file=sys.stderr)


def _interactive_approval(request: ApprovalRequest) -> bool:
    _print_approval_request(request)
    try:
        answer = input("批准烧录？(y/N)：").strip().casefold()
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
        projects_root=args.projects_root,
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
            requirement.missing_fields if requirement is not None else []
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


_JSON_APPROVAL_ERROR = (
    "JSON 模式无法交互审批，请使用 --approve-flash 预授权或改用交互模式"
)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "ports":
        return _run_ports()

    if args.command == "web":
        return _run_web(args)

    if args.command == "setup":
        return _run_setup()

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

    try:
        task = args.task if args.task is not None else input("请输入固件需求：")
        task = task.strip()
        if not task:
            print("固件需求不能为空", file=sys.stderr)
            return 2

        try:
            context = build_deepseek_runtime_context(
                project_path=project,
                target_chip=args.target,
                serial_port=args.port,
                allow_dependency_downloads=args.allow_dependency_downloads,
            )
        except (ValidationError, ValueError):
            print("运行配置无效，请检查环境变量", file=sys.stderr)
            return 2

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
        )
        if run_result.pending_approval is not None:
            # JSON 模式未预授权：工作流已在审批前暂停，只能终止。
            print(_JSON_APPROVAL_ERROR, file=sys.stderr)
            return 4

        result = run_result.state
    except KeyboardInterrupt:
        print("操作已取消", file=sys.stderr)
        return 130

    _print_result(result, args.json)
    return _exit_code_for_state(result)


if __name__ == "__main__":
    raise SystemExit(main())
