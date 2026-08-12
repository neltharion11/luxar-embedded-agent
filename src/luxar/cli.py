"""LUXAR 命令行入口：解析可信参数并调用现有 Bootstrap 与 Runner。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

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
        "repair": "修复",
        "clarification": "澄清",
        "completed": "完成",
        "failed": "失败",
    }
    print(
        f"[{labels[progress.stage]}] {progress.message}",
        file=sys.stderr,
    )


def _temporary_result_output(state: WorkflowState, json_mode: bool) -> None:
    """Task 3 会把这里替换为正式中文摘要和稳定 JSON 外壳。"""

    if json_mode:
        print('{"status":"%s"}' % state.get("status", "failed"))
    else:
        print(f"LUXAR 状态：{state.get('status', 'failed')}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project: Path = args.project

    if not project.exists() or not project.is_dir():
        print("项目路径不存在或不是目录", file=sys.stderr)
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
        result = run_workflow(
            initial_state=initial_state,
            context=context,
            progress_reporter=None if args.json else _report_progress,
        )
    except KeyboardInterrupt:
        print("操作已取消", file=sys.stderr)
        return 130

    _temporary_result_output(result, args.json)
    return {
        "completed": 0,
        "needs_clarification": 3,
        "failed": 4,
    }.get(result.get("status"), 4)
