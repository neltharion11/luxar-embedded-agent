"""LUXAR 命令行入口：解析可信参数并调用现有 Bootstrap 与 Runner。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from luxar.application.results import exit_code_for_state, state_to_result
from luxar.application.runner import WorkflowProgress, run_workflow
from luxar.application.state import WorkflowState
from luxar.bootstrap import build_deepseek_runtime_context


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("必须是正整数") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def _target_chip(value: str) -> str:
    # 芯片名只接受小写标识符，杜绝任何命令选项注入。
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise argparse.ArgumentTypeError(
            "目标芯片必须是 esp32、esp32s3 之类的小写标识符"
        )
    return value


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
        type=_target_chip,
        help="目标芯片，例如 esp32 或 esp32s3（创建任务时建议提供）",
    )
    run_parser.add_argument(
        "--max-attempts",
        type=_positive_integer,
        default=3,
    )
    run_parser.add_argument(
        "--allow-dependency-downloads",
        action="store_true",
    )
    run_parser.add_argument("--json", action="store_true")
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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
        run_result = run_workflow(
            initial_state=initial_state,
            context=context,
            progress_reporter=None if args.json else _report_progress,
        )
        result = run_result.state
    except KeyboardInterrupt:
        print("操作已取消", file=sys.stderr)
        return 130

    _print_result(result, args.json)
    return _exit_code_for_state(result)
